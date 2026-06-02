"""
Computer use tool for PAIS agents.
Called via Bash:  python3 ~/agentic_os/tools/computer.py <command> [args...]

Commands:
  screenshot [path]           — take FULL-SCREEN screenshot (active Space only — wallpaper if no windows on it)
  screenshot_window <wid> [path]
                              — take screenshot of a SPECIFIC window by its Quartz window-id.
                                Bypasses window stacking (other windows on top don't obscure capture)
                                but still requires the target window to be on the active Space.
                                Returns blank gray for off-Space windows (macOS limit).
  find_chrome_window_by_url <substring>
                              — enumerate Chrome's tabs + map to Quartz WIDs. Returns the WID +
                                screen bounds of the Chrome window containing a tab whose URL
                                matches the substring. Use this before screenshot_window /
                                find_text_in_window for job-app forms.
  find_text_in_window <wid> <text>
                              — OCR within a specific window's capture (screenshot_window then OCR).
                                Returns SCREEN-absolute click coords (window-local OCR coords are
                                translated using the window's bounds), so the returned x,y can be
                                passed directly to `click` after raising the window to front.
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
DEFAULT_SCREENSHOT = str(SCREENSHOT_DIR / "pais_screen.png")


def screenshot(path: str = DEFAULT_SCREENSHOT) -> dict:
    """Take a screenshot of the active Space and return the file path.

    LIMITATION: macOS `screencapture -x` only captures the currently active
    Space. If the window you want is on a different Space, this will return
    the active Space's wallpaper (no rendered content from off-Space windows).
    For a specific Chrome window, use `screenshot_window <wid>` instead."""
    subprocess.run(["/usr/sbin/screencapture", "-x", path], check=True)
    size = Path(path).stat().st_size
    return {"ok": True, "path": path, "size_kb": round(size / 1024, 1)}


def _window_bounds(wid: int) -> dict:
    """Look up a window's screen bounds via Quartz. Returns {x, y, w, h, onscreen, owner, title}.
    Returns {} if WID not found."""
    try:
        import Quartz
    except ImportError:
        return {}
    opts = Quartz.kCGWindowListOptionAll | Quartz.kCGWindowListExcludeDesktopElements
    for w in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID):
        if int(w.get("kCGWindowNumber", -1)) == int(wid):
            b = w.get("kCGWindowBounds", {}) or {}
            return {
                "x": int(b.get("X", 0)), "y": int(b.get("Y", 0)),
                "w": int(b.get("Width", 0)), "h": int(b.get("Height", 0)),
                "onscreen": int(w.get("kCGWindowIsOnscreen", 0)),
                "owner": w.get("kCGWindowOwnerName", ""),
                "title": w.get("kCGWindowName") or "",
            }
    return {}


def _image_looks_blank(path: str) -> bool:
    """Heuristic: a window screenshot of an off-Space window comes back as
    near-uniform gray pixels (no rendered content). Returns True if the image
    has fewer than 5 unique colors in a 10x10 grid sample — i.e., basically
    one color, almost certainly an unrendered window."""
    try:
        from PIL import Image
        img = Image.open(path).convert("RGB")
        w, h = img.size
        sample = {img.getpixel((x, y)) for x in range(5, w, max(1, w // 10))
                                       for y in range(5, h, max(1, h // 10))}
        return len(sample) < 5
    except Exception:
        return False


def screenshot_window(wid, path: str = "") -> dict:
    """Capture a SPECIFIC window by Quartz window-id via `screencapture -l`.

    This uses the macOS WindowServer's per-window backing-store capture, which
    works even when other windows are physically stacked on top of the target.
    The target window does NOT need to be frontmost — but it DOES need to be
    on the currently active Space (macOS doesn't render pixels for off-Space
    windows, so an off-Space capture returns blank gray).

    Args:
        wid: integer Quartz window-id (get one via find_chrome_window_by_url)
        path: output PNG path (default: screenshots/window_<wid>.png)

    Returns:
        {ok, path, size_kb, bounds, blank, title, owner}
        blank=True signals the window was off-Space (no rendered content)."""
    wid = int(wid)
    if not path:
        path = str(SCREENSHOT_DIR / f"window_{wid}.png")
    bounds = _window_bounds(wid)
    res = subprocess.run(
        ["/usr/sbin/screencapture", "-x", "-o", "-l", str(wid), path],
        capture_output=True, text=True
    )
    if res.returncode != 0 or not Path(path).exists():
        return {"ok": False, "error": res.stderr.strip() or "screencapture failed",
                "wid": wid, "bounds": bounds}
    size = Path(path).stat().st_size
    blank = _image_looks_blank(path)
    return {
        "ok": True, "path": path,
        "size_kb": round(size / 1024, 1),
        "bounds": bounds,
        "blank": blank,
        "title": bounds.get("title", ""),
        "owner": bounds.get("owner", ""),
    }


def find_chrome_window_by_url(substring: str) -> dict:
    """Find the Chrome window whose ANY tab URL contains `substring`. Returns
    the Quartz window-id + screen bounds so callers can screenshot/click it.

    Mechanism: AppleScript enumerates Chrome's windows/tabs to find the match,
    then we cross-reference Chrome's AppleScript window-id with Quartz's
    `kCGWindowNumber` by looking at Chrome's normal-layer windows whose owning
    PID is Chrome's PID and whose title contains the matched tab's title.

    Returns: {ok, found, wid, bounds, title, active_tab_url, active_tab_idx,
              matched_tab_idx, applescript_window_id}"""
    # 1. AppleScript: find tab containing substring, ACTIVATE it (so Quartz
    # title matches), and return identifying info. We must explicitly cast
    # numbers to strings before concatenation — AppleScript's `&` operator
    # otherwise builds a list, which `as string` flattens with ", " separators.
    safe = substring.replace('"', '\\"')
    # Use \x00 (null) as separator — cannot appear in URLs or page titles.
    script = f'''
tell application "Google Chrome"
    set matched_w to -1
    set matched_t to -1
    set matched_title to ""
    set matched_url to ""
    set active_idx to -1
    set wid_str to ""
    set SEP to (ASCII character 0)
    repeat with w from 1 to count of windows
        repeat with t from 1 to count of tabs of window w
            set u to URL of tab t of window w
            if u contains "{safe}" then
                set matched_w to w
                set matched_t to t
                set matched_url to u
                set matched_title to (title of tab t of window w)
                set wid_str to (id of window w as string)
                -- Activate the matched tab + raise the window so Quartz can
                -- cross-reference by title:
                set active tab index of window w to t
                set index of window w to 1
                set active_idx to t
                exit repeat
            end if
        end repeat
        if matched_w is not -1 then exit repeat
    end repeat
    return ((matched_w as string) & SEP & (matched_t as string) & SEP & (active_idx as string) & SEP & wid_str & SEP & matched_title & SEP & matched_url)
end tell
'''
    proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr.strip()}
    parts = proc.stdout.strip().split("\x00", 5)
    if len(parts) < 6 or parts[0] in ("-1", ""):
        return {"ok": True, "found": False, "substring": substring}
    win_idx_str, tab_idx_str, active_idx_str, applescript_wid, title, url = parts
    win_idx = int(win_idx_str)
    tab_idx = int(tab_idx_str)
    active_idx = int(active_idx_str)

    # 2. Cross-reference with Quartz to get the Quartz WID. Chrome's AppleScript
    # `id of window` is NOT the same as Quartz `kCGWindowNumber`. We match by
    # PID (Chrome's process) + title prefix (the window's currently-displayed
    # title matches whichever tab is active). Give Quartz a brief moment to
    # reflect the window-order change from `set index of window w to 1`.
    time.sleep(0.4)
    try:
        import Quartz
    except ImportError:
        return {"ok": False, "error": "Quartz not available"}

    opts = Quartz.kCGWindowListOptionAll | Quartz.kCGWindowListExcludeDesktopElements
    chrome_pid = None
    candidates = []
    for w in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID):
        if w.get("kCGWindowOwnerName") not in ("Google Chrome", "Chrome"):
            continue
        if w.get("kCGWindowLayer", 0) != 0:
            continue
        b = w.get("kCGWindowBounds", {}) or {}
        if b.get("Width", 0) < 400 or b.get("Height", 0) < 200:
            continue
        chrome_pid = chrome_pid or w.get("kCGWindowOwnerPID")
        candidates.append({
            "wid": int(w["kCGWindowNumber"]),
            "title": w.get("kCGWindowName") or "",
            "bounds": {"x": int(b.get("X", 0)), "y": int(b.get("Y", 0)),
                       "w": int(b.get("Width", 0)), "h": int(b.get("Height", 0))},
            "onscreen": int(w.get("kCGWindowIsOnscreen", 0)),
        })

    # Title matching: page titles can contain | or be truncated by Quartz with
    # an ellipsis. Match by checking if either title is a prefix of the other
    # (first 40 chars). Fall back to the first onscreen candidate if that fails
    # (we just raised the window, so it's the topmost Chrome window in Quartz).
    def _titles_match(qs_title: str, as_title: str) -> bool:
        n = 40
        return qs_title[:n].lower() == as_title[:n].lower()

    chosen = None
    if tab_idx == active_idx:
        for c in candidates:
            if c["title"] and _titles_match(c["title"], title):
                chosen = c
                break
        # Fallback: first onscreen Chrome window (we just raised the right one).
        if not chosen:
            for c in candidates:
                if c["onscreen"]:
                    chosen = c
                    break

    return {
        "ok": True, "found": True,
        "wid": chosen["wid"] if chosen else None,
        "bounds": chosen["bounds"] if chosen else None,
        "onscreen": chosen["onscreen"] if chosen else None,
        "title": chosen["title"] if chosen else title,
        "active_tab_url": url,
        "active_tab_idx": active_idx,
        "matched_tab_idx": tab_idx,
        "applescript_window_index": win_idx,
        "applescript_window_id": applescript_wid,
        "chrome_candidates_count": len(candidates),
        "needs_tab_activation": tab_idx != active_idx,
    }


def find_text_in_window(wid, search_text: str) -> dict:
    """OCR the contents of a specific window (captured via -l, ignores
    stacking), find `search_text`, and return SCREEN-absolute click coords
    so the result can be passed straight to `click`.

    Returns: {ok, found, x, y, screen_x, screen_y, wx, wy, image_path, matches}
    where wx/wy are window-local pixel coords and screen_x/screen_y are
    translated using the window's screen bounds. x/y mirror screen_x/screen_y
    for compatibility with `find_text`."""
    try:
        import pytesseract
        from PIL import Image
        import pyautogui
    except ImportError:
        return {"ok": False, "error": "pytesseract not installed."}

    wid = int(wid)
    cap = screenshot_window(wid)
    if not cap.get("ok"):
        return {"ok": False, "error": cap.get("error", "capture failed"), "wid": wid}
    if cap.get("blank"):
        return {"ok": True, "found": False, "blank": True,
                "error": "window is off-Space — no rendered content",
                "wid": wid, "bounds": cap.get("bounds")}

    img_path = cap["path"]
    bounds = cap["bounds"]
    img = Image.open(img_path)
    img_w, img_h = img.size

    # Image is at retina resolution (≈ 2× the point-space window dimensions).
    # Convert window-pixel coords to window-POINT coords (image -> window),
    # then add window-screen-origin to get absolute screen points (for click).
    pt_scale_x = bounds["w"] / img_w if img_w else 1.0
    pt_scale_y = bounds["h"] / img_h if img_h else 1.0

    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    results = []
    for i, word in enumerate(data["text"]):
        if search_text.lower() in word.lower() and data["conf"][i] > 50:
            wx_img = data["left"][i] + data["width"][i] // 2
            wy_img = data["top"][i] + data["height"][i] // 2
            wx_pt = int(wx_img * pt_scale_x)
            wy_pt = int(wy_img * pt_scale_y)
            screen_x = bounds["x"] + wx_pt
            screen_y = bounds["y"] + wy_pt
            results.append({
                "word": word, "conf": data["conf"][i],
                "wx_img": wx_img, "wy_img": wy_img,
                "wx_pt": wx_pt, "wy_pt": wy_pt,
                "screen_x": screen_x, "screen_y": screen_y,
                "x": screen_x, "y": screen_y,
            })
    if not results:
        return {"ok": True, "found": False, "matches": [], "image_path": img_path,
                "bounds": bounds, "wid": wid}
    best = max(results, key=lambda r: r["conf"])
    return {
        "ok": True, "found": True,
        "x": best["screen_x"], "y": best["screen_y"],
        "screen_x": best["screen_x"], "screen_y": best["screen_y"],
        "wx_pt": best["wx_pt"], "wy_pt": best["wy_pt"],
        "wx_img": best["wx_img"], "wy_img": best["wy_img"],
        "image_path": img_path, "bounds": bounds, "wid": wid,
        "matches": results,
    }


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
    """OCR the current screen and find coordinates of the given text.

    macOS `screencapture` writes retina PIXELS while `pyautogui.click()` takes
    screen POINTS. On a 2x Retina display that's a 2× mismatch — if we hand
    raw image coords to `click`, the click lands far off-screen. So this fn
    returns coords already converted to point-space (`x`, `y`), and also
    surfaces the raw pixel coords (`px`, `py`) for callers that need them."""
    try:
        import pytesseract
        from PIL import Image
        import pyautogui
    except ImportError:
        return {"ok": False, "error": "pytesseract not installed. Run: pip install pytesseract && brew install tesseract"}

    screenshot()
    img = Image.open(screenshot_path)
    img_w, img_h = img.size
    screen_w, screen_h = pyautogui.size()
    scale_x = screen_w / img_w if img_w else 1.0
    scale_y = screen_h / img_h if img_h else 1.0
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)

    results = []
    for i, word in enumerate(data["text"]):
        if search_text.lower() in word.lower() and data["conf"][i] > 50:
            px = data["left"][i] + data["width"][i] // 2
            py = data["top"][i] + data["height"][i] // 2
            results.append({
                "word": word, "conf": data["conf"][i],
                "x":  int(px * scale_x), "y":  int(py * scale_y),
                "px": px, "py": py,
            })

    if results:
        best = max(results, key=lambda r: r["conf"])
        return {
            "ok": True, "found": True,
            "x":  best["x"],  "y":  best["y"],   # screen points (use for click)
            "px": best["px"], "py": best["py"],  # raw image pixels
            "scale": [scale_x, scale_y],
            "matches": results,
        }
    return {"ok": True, "found": False, "matches": [],
            "scale": [scale_x, scale_y]}


def screenshot_b64(path: str = DEFAULT_SCREENSHOT) -> dict:
    """Take screenshot and return as base64 for LLM analysis."""
    screenshot(path)
    b64 = base64.b64encode(Path(path).read_bytes()).decode()
    return {"ok": True, "path": path, "base64": b64}


COMMANDS = {
    "screenshot": lambda args: screenshot(args[0] if args else DEFAULT_SCREENSHOT),
    "screenshot_window": lambda args: screenshot_window(args[0], args[1] if len(args) > 1 else ""),
    "find_chrome_window_by_url": lambda args: find_chrome_window_by_url(" ".join(args)),
    "find_text_in_window": lambda args: find_text_in_window(args[0], " ".join(args[1:])),
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
