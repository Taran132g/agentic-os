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

SYSTEM_CONTEXT = f"""You are JARVIS, Taran's personal AI agent running on his Mac.

## Computer Use
You can control Taran's Mac desktop. Use the computer tool via Bash:

  python3 {COMPUTER_TOOL} screenshot            # capture screen → saves to screenshots/jarvis_screen.png
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
  python3 {COMPUTER_TOOL} run_applescript <script>

Always take a screenshot first to understand the current screen state before clicking.
All commands return JSON. Check "ok": true before proceeding.

## Vault
Obsidian vault: ~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain
Write vault files via: python3 -c "from pathlib import Path; Path('...').write_text('...')"

## Style
- Be concise in progress messages — Taran reads on mobile
- Write substantial outputs to vault, don't return walls of text
"""


def is_running() -> bool:
    from tools.llm import get_active_proc
    proc = get_active_proc()
    return proc is not None and proc.returncode is None


async def run_task(
    task: str,
    send_telegram,
    session_id: str | None = None,
    broadcast=None,
) -> tuple[str, str, dict]:
    """
    Run a task using the unified LLM runner (Claude with Gemini fallback).
    Returns (result_text, new_session_id, usage_dict).
    """
    from tools.llm import run_llm_command

    full_prompt = f"{SYSTEM_CONTEXT}\n\n## Task\n{task}"

    res = await run_llm_command(
        prompt=full_prompt,
        broadcast=broadcast,
        session_id=session_id,
        send_telegram=send_telegram
    )

    return res["result"], res["session_id"], res["usage"]
