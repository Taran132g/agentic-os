"""
Subprocess orchestrator — runs tasks via `claude -p` (Claude Code CLI).
Claude handles all tool execution internally (Bash, file I/O, web search).
No Anthropic API key needed — uses your Claude Code subscription.
"""

import asyncio
import logging
from pathlib import Path

log = logging.getLogger(__name__)

AGENTIC_DIR = Path(__file__).parent
COMPUTER_TOOL = str(AGENTIC_DIR / "tools" / "computer.py")

def _load_lessons(max_chars: int = 2000) -> str:
    lessons = AGENTIC_DIR / "lessons.md"
    if not lessons.exists() or lessons.stat().st_size == 0:
        return ""
    text = lessons.read_text(encoding="utf-8")
    return "\n\n## Past Lessons (learn from these)\n" + text[-max_chars:]


def _load_rag_context(query: str, n_results: int = 3) -> str:
    """Semantic search the vault for context relevant to this task. Silent on failure."""
    try:
        from tools.rag import search
        ctx = search(query, n_results=n_results)
        return ("\n\n" + ctx) if ctx else ""
    except Exception:
        return ""


SYSTEM_CONTEXT = f"""You are PAIS, Taran's personal AI agent running on his Mac.

## Operating a UI — pick the right tool

There are two ways to operate a UI. Picking wrong either freezes Taran's Mac
or fails the task. Decide BEFORE you start:

- Task happens in a browser (websites, web dashboards, web apps, forms,
  scraping)? → **Playwright browser** (preferred — runs alongside Taran).
- Native macOS app, and it's scriptable (Mail, Notes, Calendar, Messages,
  Finder, Safari)? → **run_applescript** (also runs alongside Taran).
- Native macOS app that needs raw pixel clicks? → **computer tool** (this
  FREEZES Taran out of his Mac — see warning below).

### Web tasks → Playwright browser (PREFERRED)
Drives Chromium directly over CDP. It does NOT touch Taran's mouse, keyboard,
or clipboard — it runs in its own browser, so Taran keeps using his Mac while
PAIS works. The profile at ~/agentic_os/.browser_profile/ is already signed
into the services Taran has bootstrapped (cookies persist across runs).

Put the WHOLE browser task in one Python script — page state lives in the
process, so multi-step flows must share one `session()` block:

  cd ~/agentic_os && python3 - <<'PY'
  from tools.pais_browser import session
  # headed=True: a visible window Taran can watch and take over for 2FA/CAPTCHA.
  # headed=False: silent background run. Neither touches his cursor.
  with session(headed=True) as page:
      page.goto("https://example.com", wait_until="domcontentloaded")
      page.click("text=Sign in")
      page.fill("#email", "someone@example.com")
      page.screenshot(path="screenshots/web.png")
      print(page.title())
  PY

If a service isn't logged in, do NOT try to log in via Playwright — tell Taran
to run, in his own terminal:
  python3 ~/agentic_os/tools/pais_browser.py bootstrap-login --service NAME --url SIGNIN_URL

### Native macOS apps → computer tool
WARNING: the computer tool drives Taran's PHYSICAL mouse and keyboard, and
`type` overwrites his clipboard. While it runs, Taran CANNOT use his Mac.
Treat every computer-tool task as foreground/blocking: tell Taran up front
"don't touch the computer until this finishes." Never use it for web tasks —
use Playwright instead. Prefer run_applescript whenever the app is scriptable;
AppleScript talks to apps directly and does not grab the cursor.

  python3 {COMPUTER_TOOL} screenshot            # capture screen → saves to screenshots/pais_screen.png
  python3 {COMPUTER_TOOL} screen_size           # get screen dimensions
  python3 {COMPUTER_TOOL} click <x> <y>         # left click at coordinates
  python3 {COMPUTER_TOOL} right_click <x> <y>   # right click
  python3 {COMPUTER_TOOL} double_click <x> <y>  # double click
  python3 {COMPUTER_TOOL} type <text>           # type text (uses clipboard paste)
  python3 {COMPUTER_TOOL} key <combo>           # key press, e.g. "cmd+c", "enter", "tab"
  python3 {COMPUTER_TOOL} scroll <x> <y> <n>   # scroll at position (positive=up)
  python3 {COMPUTER_TOOL} drag <x1> <y1> <x2> <y2>
  python3 {COMPUTER_TOOL} window_list          # list open windows
  python3 {COMPUTER_TOOL} focus_window <title> # bring window to front
  python3 {COMPUTER_TOOL} open <App Name>      # launch/activate an app
  python3 {COMPUTER_TOOL} find_text <text>     # OCR screen to find text coordinates
  python3 {COMPUTER_TOOL} run_applescript <script>   # preferred for scriptable apps

Always take a screenshot first to understand the current screen state before clicking.
All commands return JSON. Check "ok": true before proceeding.

## Vault
Obsidian vault: ~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain
Write vault files via: python3 -c "from pathlib import Path; Path('...').write_text('...')"

## Style
- Be concise in progress messages — Taran reads on mobile
- Write substantial outputs to vault, don't return walls of text
"""


CHAT_CONTEXT = f"""You are PAIS, Taran's personal AI assistant running on his Mac.

## Mode
Chat mode — Taran is on his iPhone. Be conversational and concise.

## Vault (your memory)
Taran's Obsidian knowledge base:
  ~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain

For any question about his projects, finances, notes, or goals — read the vault:
  cat "$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain/index.md"
Then read the specific pages that are relevant.

Write via Bash (iCloud sync requires this):
  BASE="$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain"
  cat > "$BASE/path/to/Note.md" << 'EOF'
  content
  EOF

## Style
- Short replies unless detail is needed
- Skip narration like "I'll now read..." — just do it and report findings
- Plain text only (markdown won't render in the chat UI)
- For long outputs, write to vault and give a 2-3 line summary
"""


async def run_chat(text: str, broadcast=None, task_id: str | None = None) -> tuple[str, str, dict]:
    """Run a conversational chat task — no routing, no verification step."""
    from tools.llm import run_llm_command
    rag = _load_rag_context(text, n_results=2)
    full_prompt = f"{CHAT_CONTEXT}{_load_lessons()}{rag}\n\n## Message\n{text}"
    res = await run_llm_command(
        prompt=full_prompt, broadcast=broadcast,
        agent_name="general", task_id=task_id,
    )
    return res["result"], res["session_id"], res["usage"]


def is_running() -> bool:
    from tools.llm import get_active_proc
    proc = get_active_proc()
    return proc is not None and proc.returncode is None


async def run_task(
    task: str,
    send_telegram,
    session_id: str | None = None,
    broadcast=None,
    task_id: str | None = None,
) -> tuple[str, str, dict]:
    """
    Run a task using the unified LLM runner.
    Returns (result_text, new_session_id, usage_dict).
    """
    from tools.llm import run_llm_command

    rag = _load_rag_context(task, n_results=3)
    full_prompt = f"{SYSTEM_CONTEXT}{_load_lessons()}{rag}\n\n## Task\n{task}"

    res = await run_llm_command(
        prompt=full_prompt,
        broadcast=broadcast,
        session_id=session_id,
        send_telegram=send_telegram,
        agent_name="general",
        task_id=task_id,
    )

    return res["result"], res["session_id"], res["usage"]
