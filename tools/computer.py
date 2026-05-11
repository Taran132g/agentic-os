"""
Computer use tool for JARVIS agents.
Called via Bash:  python3 ~/agentic_os/tools/computer.py <command> [args...]

Commands:
  screenshot [path]           — take screenshot, save to path (default: /tmp/jarvis_screen.png)
  click <x> <y>               — left click at screen coordinates
  right_click <x> <y>         — right click at coordinates
  double_click <x> <y>        — double click at coordinates
  move <x> <y>                — move mouse to coordinates
  type <text>                 — type text (use quotes for spaces)
  key <key>                   — press a key or combo, e.g. "cmd+c", "enter", "tab"
  scroll <x> <y> <delta>      — scroll at position, positive=up negative=down
  drag <x1> <y1> <x2> <y2>   — click-drag from (x1,y1) to (x2,y2)
  find_text <text>            — OCR the screen and return coordinates of text
  screen_size                 — print current screen dimensions
  open <app_name>             — open an app via osascript
  run_applescript <script>    — run arbitrary AppleScript
  window_list                 — list visible window titles and their app names
  focus_window <title>        — bring window with matching title to front
"""

import sys
import os
import subprocess
import json
import base64
import time
import shlex
from pathlib import Path

SCREENSHOT_DIR = Path(__file__).parent.parent / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)
DEFAULT_SCREENSHOT = str(SCREENSHOT_DIR / "jarvis_screen.png")


def screenshot(path: str = DEFAULT_SCREENSHOT) -> dict:
    """Take a screenshot and return the file path."""
    subprocess.run(["screencapture", "-x", path], check=True)
    size = Path(path).stat().st_size
    return {"ok": True, "path": path, "size_kb": round(size / 1024, 1)}


def click(x: int, y: int) -> dict:
    import pyautogui
    pyautogui.click(x, y)
    return {"ok": True, "action": "click", "x": x, "y": y}


def right_click(x: int, y: int) -> dict:
    import pyautogui
    pyautogui.rightClick(x, y)
    return {"ok": True, "action": "right_click", "x": x, "y": y}


def double_click(x: int, y: int) -> dict:
    import pyautogui
    pyautogui.doubleClick(x, y)
    return {"ok": True, "action": "double_click", "x": x, "y": y}


def move(x: int, y: int) -> dict:
    import pyautogui
    pyautogui.moveTo(x, y)
    return {"ok": True, "action": "move", "x": x, "y": y}


def type_text(text: str) -> dict:
    import pyautogui
    # Use pyperclip+paste for reliability with special chars
    import pyperclip
    pyperclip.copy(text)
    pyautogui.hotkey("cmd", "v")
    time.sleep(0.1)
    return {"ok": True, "action": "type", "text": text[:80]}


def key(combo: str) -> dict:
    import pyautogui
    # "cmd+c" → hotkey("cmd", "c"), "enter" → press("enter")
    parts = [p.strip() for p in combo.split("+")]
    if len(parts) > 1:
        pyautogui.hotkey(*parts)
    else:
        pyautogui.press(parts[0])
    return {"ok": True, "action": "key", "combo": combo}


def scroll(x: int, y: int, delta: int) -> dict:
    import pyautogui
    pyautogui.scroll(delta, x=x, y=y)
    return {"ok": True, "action": "scroll", "x": x, "y": y, "delta": delta}


def drag(x1: int, y1: int, x2: int, y2: int, duration: float = 0.5) -> dict:
    import pyautogui
    pyautogui.moveTo(x1, y1)
    pyautogui.dragTo(x2, y2, duration=duration, button="left")
    return {"ok": True, "action": "drag", "from": [x1, y1], "to": [x2, y2]}


def screen_size() -> dict:
    import pyautogui
    w, h = pyautogui.size()
    return {"ok": True, "width": w, "height": h}


def open_app(app_name: str) -> dict:
    script = f'tell application "{app_name}" to activate'
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip()}
    return {"ok": True, "action": "open", "app": app_name}


def run_applescript(script: str) -> dict:
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return {
        "ok": result.returncode == 0,
        "output": result.stdout.strip(),
        "error": result.stderr.strip() if result.returncode != 0 else None,
    }


def window_list() -> dict:
    script = """
tell application "System Events"
    set win_list to {}
    repeat with proc in (every process whose background only is false)
        set proc_name to name of proc
        try
            repeat with w in (every window of proc)
                set end of win_list to {proc_name & " | " & name of w}
            end repeat
        end try
    end repeat
    return win_list
end tell
"""
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip()}
    windows = [w.strip() for w in result.stdout.strip().split(",") if w.strip()]
    return {"ok": True, "windows": windows}


def focus_window(title: str) -> dict:
    script = f"""
tell application "System Events"
    repeat with proc in (every process whose background only is false)
        try
            repeat with w in (every window of proc)
                if name of w contains "{title}" then
                    set frontmost of proc to true
                    perform action "AXRaise" of w
                    return "focused"
                end if
            end repeat
        end try
    end repeat
    return "not found"
end tell
"""
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    found = result.returncode == 0 and "focused" in result.stdout
    return {"ok": found, "action": "focus_window", "title": title}


def find_text(search_text: str, screenshot_path: str = DEFAULT_SCREENSHOT) -> dict:
    """OCR the current screen and find coordinates of the given text."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return {"ok": False, "error": "pytesseract not installed. Run: pip install pytesseract && brew install tesseract"}

    screenshot()
    img = Image.open(screenshot_path)
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)

    results = []
    for i, word in enumerate(data["text"]):
        if search_text.lower() in word.lower() and data["conf"][i] > 50:
            x = data["left"][i] + data["width"][i] // 2
            y = data["top"][i] + data["height"][i] // 2
            results.append({"word": word, "x": x, "y": y, "conf": data["conf"][i]})

    if results:
        best = max(results, key=lambda r: r["conf"])
        return {"ok": True, "found": True, "x": best["x"], "y": best["y"], "matches": results}
    return {"ok": True, "found": False, "matches": []}


def screenshot_b64(path: str = DEFAULT_SCREENSHOT) -> dict:
    """Take screenshot and return as base64 for LLM analysis."""
    screenshot(path)
    b64 = base64.b64encode(Path(path).read_bytes()).decode()
    return {"ok": True, "path": path, "base64": b64}


COMMANDS = {
    "screenshot": lambda args: screenshot(args[0] if args else DEFAULT_SCREENSHOT),
    "screenshot_b64": lambda args: screenshot_b64(args[0] if args else DEFAULT_SCREENSHOT),
    "click": lambda args: click(int(args[0]), int(args[1])),
    "right_click": lambda args: right_click(int(args[0]), int(args[1])),
    "double_click": lambda args: double_click(int(args[0]), int(args[1])),
    "move": lambda args: move(int(args[0]), int(args[1])),
    "type": lambda args: type_text(" ".join(args)),
    "key": lambda args: key(args[0]),
    "scroll": lambda args: scroll(int(args[0]), int(args[1]), int(args[2])),
    "drag": lambda args: drag(int(args[0]), int(args[1]), int(args[2]), int(args[3])),
    "screen_size": lambda args: screen_size(),
    "open": lambda args: open_app(" ".join(args)),
    "run_applescript": lambda args: run_applescript(" ".join(args)),
    "window_list": lambda args: window_list(),
    "focus_window": lambda args: focus_window(" ".join(args)),
    "find_text": lambda args: find_text(args[0], args[1] if len(args) > 1 else DEFAULT_SCREENSHOT),
}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "Usage: computer.py <command> [args...]"}))
        sys.exit(1)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd not in COMMANDS:
        print(json.dumps({"ok": False, "error": f"Unknown command: {cmd}. Available: {list(COMMANDS.keys())}"}))
        sys.exit(1)

    try:
        result = COMMANDS[cmd](args)
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e), "command": cmd, "args": args}))
        sys.exit(1)
