"""
Drive Gemini-in-Chrome to fill a job-application form — WORKING pipeline
(proven end-to-end on Celonis 2026-05-28).

This replaces the old OCR-full-screen / pyautogui-cmd+v / "diamond" approach
that never produced a fill. The breakthroughs baked in here:

  * macOS Spaces: `screencapture -x` only sees the active Space. We capture the
    SPECIFIC Chrome window via `screencapture -l <wid>` (tools/computer.py
    screenshot_window), which works even when other windows cover Chrome and
    even for a freshly-spawned off-Space window (its backing store is warm).
  * `make new window` spawns on the CURRENT Space (unlike `open -a <url>` which
    appends a tab to an existing, possibly off-Space window).
  * pyautogui's cmd+v is broken on this Mac (types literal "v"). We paste via
    AppleScript `keystroke "v" using command down`.
  * Gemini's agentic mode is the "Start task" button (no multi-colored diamond
    in the 2026 UI). Flow: Ask Gemini → paste brief → Enter → "Start task".

The deterministic happy path (open → capture → panel → paste → Start task →
poll) lives here. Gemini PAUSES on sensitive fields ("Waiting for your help")
and may pick wrong dropdown values — those corrections need an LLM in the loop;
this function flags a pause in its result so the caller (career agent / Taran)
can handle it. The full agent-driven runbook with corrections is
~/agentic_os/JOB_APP_AGENT.md.
"""

import base64
import json
import logging
import os
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

PAIS_DIR    = Path(__file__).parent.parent
COMPUTER    = str(PAIS_DIR / "tools" / "computer.py")
RESUME_PATH = str(PAIS_DIR / "resume.pdf")
BRIEF_MD    = PAIS_DIR / "JOB_APP_BRIEF.md"
SHOT_DIR    = PAIS_DIR / "screenshots"
SHOT_DIR.mkdir(exist_ok=True)


# ── thin wrappers ─────────────────────────────────────────────────────────────

def _osa(script: str, timeout: int = 20) -> dict:
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=timeout)
        return {"ok": r.returncode == 0, "out": r.stdout.strip(),
                "err": r.stderr.strip() if r.returncode else ""}
    except Exception as e:
        return {"ok": False, "out": "", "err": str(e)}


def _computer(cmd: str, *args: str, timeout: int = 40) -> dict:
    try:
        p = subprocess.run(["python3", COMPUTER, cmd, *args],
                           capture_output=True, text=True, timeout=timeout)
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": "non-JSON output", "stdout": p.stdout[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _activate_chrome():
    _osa('tell application "Google Chrome" to activate')
    time.sleep(0.4)


def _paste_text(text: str):
    """Reliable paste: stage on clipboard, then AppleScript Cmd+A (clear) + Cmd+V.
    pyautogui's cmd+v types a literal 'v' on this Mac, so we never use it."""
    subprocess.run(["pbcopy"], input=text.encode(), check=False)
    time.sleep(0.2)
    _osa('tell application "System Events" to keystroke "a" using command down')
    time.sleep(0.2)
    _osa('tell application "System Events" to keystroke "v" using command down')
    time.sleep(1.0)


def _press_return():
    _osa('tell application "System Events" to key code 36')


# ── brief ───────────────────────────────────────────────────────────────────

def _build_brief(url: str, company: str, role: str) -> str:
    """Generate the Gemini brief from JOB_APP_BRIEF.md (single source of truth),
    substituting the job placeholders."""
    try:
        tmpl = BRIEF_MD.read_text()
    except Exception:
        tmpl = ("You are helping me apply to <<COMPANY>> for the <<ROLE>> role. "
                "The job page is at <<URL>>. Drive the page yourself and fill it "
                "using my details. Do NOT click Submit.")
    return (tmpl.replace("<<URL>>", url)
                .replace("<<COMPANY>>", company)
                .replace("<<ROLE>>", role))


# ── pipeline steps ────────────────────────────────────────────────────────────

def _spawn_window(url: str) -> bool:
    """Open the URL as a NEW Chrome window — `make new window` spawns on the
    CURRENT macOS Space (the key Space fix)."""
    safe = url.replace('"', '\\"')
    r = _osa(f'''tell application "Google Chrome"
    activate
    set w to make new window
    set URL of active tab of w to "{safe}"
    set index of w to 1
end tell''')
    return r["ok"]


def _find_wid(url: str) -> dict:
    """Return the Quartz WID + bounds of the Chrome window holding the URL.
    Auto-activates the matching tab + raises the window."""
    slug = url.rstrip("/").split("/")[-1][:20] or url
    r = _computer("find_chrome_window_by_url", slug)
    if r.get("ok") and r.get("found") and r.get("wid"):
        return {"wid": r["wid"], "bounds": r.get("bounds") or {},
                "onscreen": r.get("onscreen")}
    return {}


def _capture(wid, name: str) -> dict:
    """screenshot_window — works through window stacking. blank=True => off-Space."""
    path = str(SHOT_DIR / f"jobfill_{name}.png")
    return _computer("screenshot_window", str(wid), path)


def _space_search(wid) -> bool:
    """If the window is off-Space (blank capture), cycle Ctrl+Right then
    Ctrl+Left until screenshot_window returns non-blank. Returns True if found."""
    def blank() -> bool:
        c = _computer("screenshot_window", str(wid))
        return bool(c.get("blank"))
    if not blank():
        return True
    for code in [124] * 5 + [123] * 10:   # 5 right, then 10 left (5 to reset + 5 to search)
        _osa(f'tell application "System Events" to key code {code} using control down')
        time.sleep(1.4)
        if not blank():
            return True
    return False


def _open_gemini_panel(wid) -> bool:
    """OCR the 'Ask Gemini' toolbar button (window-scoped), activate Chrome,
    click it, verify the panel opened via the 'Taranveer' greeting."""
    g = _computer("find_text_in_window", str(wid), "Gemini")
    if not g.get("found"):
        return False
    _activate_chrome()
    _computer("click", str(g["x"]), str(g["y"]))
    time.sleep(2.5)
    greet = _computer("find_text_in_window", str(wid), "Taranveer")
    return bool(greet.get("found"))


def _chat_input_xy(bounds: dict) -> tuple[int, int]:
    """Computed chat-input location: bottom of the side panel. OCR of the
    low-contrast placeholder is unreliable, so we compute from window bounds."""
    x = int(bounds.get("x", 122) + 0.81 * bounds.get("w", 1300))
    y = int(bounds.get("y", 102) + 0.96 * bounds.get("h", 820))
    return x, y


def _click_start_task(wid) -> bool:
    # "Start task" button has white text on a blue bg — OCR often misses "Start"
    # but catches "task". Fallback: find "Cancel" (the button to its left) and
    # click ~200px right at the same y to land on "Start task".
    for needle in ("Start task", "Start", "task"):
        s = _computer("find_text_in_window", str(wid), needle)
        if s.get("found"):
            _activate_chrome()
            _computer("click", str(s["x"]), str(s["y"]))
            time.sleep(3)
            return True
    cancel = _computer("find_text_in_window", str(wid), "Cancel")
    if cancel.get("found"):
        _activate_chrome()
        _computer("click", str(int(cancel["x"]) + 200), str(cancel["y"]))
        time.sleep(3)
        return True
    return False


def _final_b64(wid, job_id: str) -> tuple[str, bytes]:
    path = SHOT_DIR / f"{job_id}_final.png"
    _computer("screenshot_window", str(wid), str(path))
    if path.exists():
        b = path.read_bytes()
        return base64.b64encode(b).decode(), b
    return "", b""


# ── verify + notify (fire-and-verify model, 2026-05-29) ─────────────────────────

def _task_engaged(wid) -> bool:
    """~60s after clicking Start task, the button is consumed once Gemini begins
    browsing. If 'Start task'/'Start' is still on screen the click didn't engage."""
    for needle in ("Start task", "Start"):
        if _computer("find_text_in_window", str(wid), needle).get("found"):
            return False
    return True


def _load_env():
    """Populate TELEGRAM_* from ~/agentic_os/.env when not already in the
    environment (standalone `python3 -c` runs don't load config.py)."""
    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        return
    envf = PAIS_DIR / ".env"
    if not envf.exists():
        return
    for line in envf.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _send_telegram(caption: str, photo_path: str | None = None) -> bool:
    """Text Taran (with the final screenshot if we have one). Best-effort."""
    _load_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.warning("[browser_fill] Telegram creds missing; skipping notify")
        return False
    try:
        import requests
        if photo_path and Path(photo_path).exists():
            with open(photo_path, "rb") as f:
                r = requests.post(
                    f"https://api.telegram.org/bot{token}/sendPhoto",
                    files={"photo": f},
                    data={"chat_id": chat_id, "caption": caption[:1024]},
                    timeout=60)
        else:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat_id, "text": caption[:4096]},
                timeout=30)
        return bool(r.ok and r.json().get("ok"))
    except Exception as e:
        log.warning("[browser_fill] Telegram send failed: %s", e)
        return False


# ── public entry point ────────────────────────────────────────────────────────

def browser_fill(job: dict, agent: str = "gemini", verify_seconds: int = 60,
                 notify: bool = True, poll: bool = False,
                 timeout_seconds: int = 420, poll_seconds: int = 180) -> dict:
    """Drive Gemini-in-Chrome to fill `job`'s application form.

    Default = fire-and-verify (token-light): paste the brief, click Start task,
    wait `verify_seconds`, confirm the task engaged, Telegram Taran either way
    (with the final screenshot), and return. No long polling / no autonomous
    corrections — Taran reviews the open Chrome tab, fixes small errors, and
    submits himself. Set `notify=False` when the caller sends its own Telegram
    (e.g. career_workflow). Set `poll=True` for the legacy babysitting loop.

    Returns {ok, error, platform, fields_filled, status, screenshot_b64,
             screenshot_bytes, wid}. `status` is one of:
      'running'      — task engaged after the verify window (default-mode success)
      'start_failed' — 'Start task' still on screen after the verify window
      'task_done'    — (poll mode) Gemini reported completion
      'needs_input'  — (poll mode) Gemini paused ("Waiting for your help")
      'timeout'      — (poll mode) polling budget exhausted
    """
    url     = (job.get("url") or "").strip()
    company = job.get("company", "the company")
    role    = job.get("role", "the role")
    job_id  = str(job.get("id", "job"))

    def fail(msg, status="error", wid=None):
        b64, b = (_final_b64(wid, job_id) if wid else ("", b""))
        return {"ok": False, "error": msg, "platform": "gemini", "status": status,
                "fields_filled": [], "screenshot_b64": b64, "screenshot_bytes": b,
                "wid": wid}

    if not url.startswith("http"):
        return fail("job has no application URL")

    # 1. New window on the current Space
    log.info("[browser_fill] spawning window for %s", url[:60])
    if not _spawn_window(url):
        return fail("failed to open Chrome window")
    time.sleep(4)

    # 2. Locate the window (activates tab + raises)
    loc = _find_wid(url)
    if not loc:
        return fail("could not locate the Chrome window for the job URL")
    wid, bounds = loc["wid"], loc["bounds"]
    log.info("[browser_fill] window wid=%s onscreen=%s", wid, loc.get("onscreen"))

    # 3. Ensure it's capturable (on the active Space)
    cap = _capture(wid, f"{job_id}_open")
    if cap.get("blank"):
        log.info("[browser_fill] window off-Space — running Space search")
        if not _space_search(wid):
            return fail("Celonis window is on an unreachable Space (minimized or "
                        "fullscreen-Space). Restart Chrome with the URL as a fresh "
                        "window on the current Space.", status="off_space", wid=wid)

    # 4. Open Gemini side panel
    if not _open_gemini_panel(wid):
        return fail("could not open the Ask Gemini side panel", wid=wid)

    # 5. Paste the brief into the chat input + send
    brief = _build_brief(url, company, role)
    cx, cy = _chat_input_xy(bounds)
    _activate_chrome()
    _computer("click", str(cx), str(cy))
    time.sleep(0.5)
    _paste_text(brief)
    _press_return()
    time.sleep(4)

    # 6. Click "Start task" (Gemini's agentic activation)
    if not _click_start_task(wid):
        # Gemini may not have surfaced the plan yet; give it a moment and retry once.
        time.sleep(4)
        if not _click_start_task(wid):
            return fail("Gemini did not surface a 'Start task' button — it may have "
                        "answered conversationally instead of proposing a browse plan.",
                        status="no_start_task", wid=wid)

    # 7. Fire-and-verify (default, token-light): wait, confirm the task engaged,
    #    Telegram Taran either way, return. Taran reviews + fixes + submits.
    if not poll:
        log.info("[browser_fill] task started; verifying in %ds", verify_seconds)
        time.sleep(verify_seconds)
        engaged = _task_engaged(wid)
        status = "running" if engaged else "start_failed"
        b64, b = _final_b64(wid, job_id)
        if notify:
            shot = str(SHOT_DIR / f"{job_id}_final.png")
            if engaged:
                caption = (f"✅ {company} — {role}\n"
                           f"Gemini is filling the form. Review the fields, fix any "
                           f"small errors, upload the resume, and submit.")
            else:
                caption = (f"⚠️ {company} — {role}\n"
                           f"'Start task' may not have engaged. Open the Chrome "
                           f"window and click Start task / check the form.")
            _send_telegram(caption, shot)
        return {
            "ok": engaged,
            "error": "" if engaged else "Start task may not have engaged",
            "platform": "gemini", "status": status, "fields_filled": [],
            "screenshot_b64": b64, "screenshot_bytes": b, "wid": wid,
        }

    # 7b. (legacy) Poll for completion / pause — only when poll=True.
    log.info("[browser_fill] task started; polling every %ds (budget %ds)",
             poll_seconds, timeout_seconds)
    start = time.time()
    status = "timeout"
    while time.time() - start < timeout_seconds:
        time.sleep(poll_seconds)
        done = _computer("find_text_in_window", str(wid), "done")
        if done.get("found"):
            status = "task_done"; break
        waiting = _computer("find_text_in_window", str(wid), "Waiting")
        if waiting.get("found"):
            status = "needs_input"; break

    b64, b = _final_b64(wid, job_id)
    ok = status in ("task_done", "needs_input")
    err = "" if ok else (
        "Timed out before Gemini reported done — check the Chrome window."
        if status == "timeout" else "")
    if status == "needs_input":
        err = ("Gemini paused for input (sensitive-field confirmation or a wrong "
               "dropdown pick). Follow JOB_APP_AGENT.md to answer via the chat, "
               "or review the window manually.")
    return {
        "ok": ok, "error": err, "platform": "gemini", "status": status,
        "fields_filled": [],   # no DOM access; see screenshot for filled state
        "screenshot_b64": b64, "screenshot_bytes": b, "wid": wid,
    }


# Backwards-compat shim — older callers imported cic_fill.
def cic_fill(job: dict, *a, **kw) -> dict:
    return browser_fill(job, *a, **kw)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    test = {"id": "smoke", "company": "Celonis", "role": "Engineering Intern",
            "url": sys.argv[1] if len(sys.argv) > 1
                   else "https://job-boards.greenhouse.io/celonis/jobs/7738530003"}
    res = browser_fill(test, verify_seconds=60)
    print(json.dumps({k: v for k, v in res.items() if k != "screenshot_bytes"}, indent=2))
