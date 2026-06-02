# Job Application Monitor — Runbook

> Read this file at the start of every job-application task.

## Your role

You are the **Job Application Monitor**. Your job is to orchestrate Gemini in Chrome to fill a job application form.

**Primary path — use `browser_fill.py` first:**
```bash
cd ~/agentic_os && python3 -c "
import json, logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
from tools.browser_fill import browser_fill
job = {'id': 'JOB_ID', 'company': 'COMPANY', 'role': 'ROLE', 'url': 'URL'}
result = browser_fill(job, verify_seconds=60)   # fire-and-verify (token-light)
print(json.dumps({k:v for k,v in result.items() if k != 'screenshot_bytes'}, indent=2))
"
```
**Fire-and-verify is the default (2026-05-29).** browser_fill pastes the brief, clicks Start task, waits 60s, confirms the task engaged, Telegrams Taran (with the final screenshot) either way, and returns immediately. **Do NOT babysit or poll** — no autonomous corrections, no 5–8 minute monitor loop. Taran reviews the open Chrome tab, fixes small errors, uploads the resume, and submits himself. This is deliberate, to save tokens and let multiple applications fire fast.

- `status == 'running'` → task engaged; you're done. Move to the next job.
- `status == 'start_failed'` → "Start task" still on screen after 60s. Telegram already warned Taran; move on (don't loop).

Legacy babysitting (poll loop + corrections) is still available via `browser_fill(job, poll=True, timeout_seconds=600, poll_seconds=180)` — only use it if Taran explicitly asks to babysit a single high-value application. The Step 4–6 correction/monitor sections below describe that legacy path.

**Fallback — manual step-by-step** (only if browser_fill.py fails entirely): use `~/agentic_os/tools/computer.py`. Every step: screenshot → read → verify → act.

## Inputs (extract from the task prompt)
- `URL` — the application page URL
- `COMPANY` — the company name (e.g. "Celonis")
- `ROLE` — the position title (e.g. "Engineering Intern")
- `SUBMIT` — optional, default `false`. If `true`, you will click the form's Submit button at the end (Step 8). Only set to `true` when Taran explicitly tells you to submit.

If `URL` / `COMPANY` / `ROLE` are missing from the task, ask Taran via Telegram and stop. Do not guess.

## Hard rules
- **First action: Telegram Taran** — "Starting job-app fill for {COMPANY}. Don't touch the computer for ~6 min — I'm driving the mouse and keyboard."
- **Use `tools/computer.py` only** for clicks/typing. Do NOT import `browser_fill.py`. Do NOT use Playwright.
- **Never click Submit.** Leave the filled form open for Taran's review.
- **Screenshot every step** to `~/agentic_os/screenshots/job_app_<TIMESTAMP>_<step>.png`. Read each screenshot back before the next action.
- **One Gemini window.** If multiple Chrome windows exist, spawn a fresh one (step 2) so the target is unambiguous.

---

## Steps

### Step 0 — Telegram warning
Send the warning above before doing anything that grabs the cursor.

### Step 1 — Stage the brief on the clipboard
Substitute `<<URL>>`, `<<COMPANY>>`, `<<ROLE>>` in `JOB_APP_BRIEF.md` and pipe to clipboard:
```bash
URL="<the URL>"
COMPANY="<the company>"
ROLE="<the role>"
sed -e "s|<<URL>>|$URL|g" -e "s|<<COMPANY>>|$COMPANY|g" -e "s|<<ROLE>>|$ROLE|g" \
    ~/agentic_os/JOB_APP_BRIEF.md | pbcopy
pbpaste | head -c 80   # sanity check — should start with "You are helping me apply to $COMPANY..."
```

### Capture model (read this before Steps 2-7)

PAIS now has window-specific capture commands that bypass window stacking. Use these for ALL Celonis interactions — do NOT use `screenshot` (full-screen) or `find_text` (full-screen OCR) on the application page.

| Command | Purpose |
|---|---|
| `find_chrome_window_by_url <slug>` | Finds the Chrome window containing a tab whose URL matches `<slug>`. AUTO-ACTIVATES that tab + raises the window in Chrome's z-order. Returns Quartz WID, screen bounds, and onscreen flag. |
| `screenshot_window <wid>` | Captures that specific window's rendered content via `screencapture -l <wid>` — works even when other windows (Terminal, etc.) cover Chrome. Returns `blank: true` if the window is on a different Space. |
| `find_text_in_window <wid> <text>` | OCRs the window-specific capture, returns SCREEN-absolute click coords (already translated using the window's bounds). |

Why this matters: `screenshot` only captures the active Space and is obscured by stacked windows. `screenshot_window <wid>` captures the Chrome window's content directly from its backing store, regardless of whether Terminal/Slack/etc. are on top of it. The `blank` flag is the cleanest "is this window on the current Space" detector.

### Step 2 — Open the job page in Chrome on the CURRENT Space + MAIN display
**Critical:** Taran has ONE monitor but MULTIPLE macOS Spaces (each Space can have its own wallpaper). His current Space's wallpaper is a big red "REDDIT VERDICT" card. Other Spaces may show different wallpapers (vineyard, default Sonoma, etc.). If a screenshot shows ANY wallpaper as the dominant content, the Chrome window is on a different Space, in fullscreen (which gives it its own private Space), or minimized.

Three-part Step 2: open the URL, then force Chrome's front window onto the current Space + main display.

```bash
# 2a — Open the URL as a NEW Chrome window. CRITICAL: `make new window` always
# spawns on the CURRENT macOS Space — this is the root-cause fix for the
# multi-Space problem. (`open -a "Google Chrome" <URL>` instead appends a tab
# to an existing window which may be on a DIFFERENT Space, which is why earlier
# attempts kept screenshotting wallpaper.) We accept that this may create a
# duplicate Celonis tab — find_chrome_window_by_url will pick the on-Space one.
osascript <<EOF
tell application "Google Chrome"
    activate
    set newWin to make new window
    set URL of active tab of newWin to "$URL"
    set index of newWin to 1
end tell
EOF
sleep 4   # let the page load

# 2b — Exit fullscreen if engaged (fullscreen Chrome lives in its own Space):
osascript -e 'tell application "System Events" to tell process "Google Chrome"
    try
        click menu item "Exit Full Screen" of menu "View" of menu bar 1
    end try
end tell'
sleep 2

# 2c — Un-miniaturize ALL Chrome windows + force front window onto main display
# at a known sane position. Setting `bounds` in AppleScript uses global coords
# with {0,0} at top-left of the main display (where pyautogui screenshots),
# which physically pulls the window to where the agent can see it:
osascript -e 'tell application "Google Chrome"
    activate
    if (count of windows) > 0 then
        set miniaturized of every window to false
        try
            set bounds of front window to {100, 80, 1400, 900}
        end try
    end if
end tell'
sleep 2

# 2d — Locate the Celonis Chrome window + verify it's capturable.
# `find_chrome_window_by_url` auto-activates the matched tab and raises the
# window, so we don't need separate AppleScript or bounds-setting.
TS=$(date +%s)
SLUG=$(echo "$URL" | awk -F/ '{print $NF}' | head -c 20)
LOC=$(python3 ~/agentic_os/tools/computer.py find_chrome_window_by_url "$SLUG")
echo "$LOC"
WID=$(echo "$LOC" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("wid") or "")')
if [ -z "$WID" ]; then
    echo "ABORT: Celonis tab not found in any Chrome window"
    exit 1
fi
# Window-specific capture: shows Celonis even if Terminal/Slack are on top.
CAP=$(python3 ~/agentic_os/tools/computer.py screenshot_window "$WID" ~/agentic_os/screenshots/job_app_${TS}_02_celonis.png)
echo "$CAP"
BLANK=$(echo "$CAP" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("blank"))')
```
Read `02_celonis.png`. Expected: Celonis page (logo, "Engineering Intern", "Apply" button, "Ask Gemini" button at top-right).
- If `BLANK=True` → Celonis window is on a different macOS Space. **Run Step 2.5 (Space search)**, then redo the `find_chrome_window_by_url` + `screenshot_window` pair on the new Space.
- If image looks blank but BLANK=False → unlikely; treat as Step 2.5 trigger anyway.
- Otherwise proceed to Step 3.

### Step 2.5 — Space search (rigid Ctrl+Right then Ctrl+Left cycling)

**RUN THE SCRIPT BELOW VERBATIM. DO NOT modify, do not add extra screenshots between iterations, do not try alternative recoveries (no exit-fullscreen, no Cmd+Q, no Mission Control), do not interpret intermediate screenshots.** The script does everything. Your only job is to invoke it, then read its final stdout output to know whether it succeeded.

**Why this exists:** macOS Spaces — Chrome's windows may live on a different Space than the active one. `screencapture` only captures the active Space. macOS Spaces cannot be enumerated programmatically and Ctrl+Right/Left do not loop (they stop at the boundary), so we cycle right N times then left N times from start to cover both sides.

Save the entire block below to `/tmp/space_search.sh` and run it. **Do NOT inline-execute or modify steps:**

The detector is the WID `blank` flag — NOT full-screen OCR. `screenshot_window <wid>` returns `blank: true` while the window is off-Space and `blank: false` the moment its Space becomes active. This is immune to window stacking (Terminal/Slack on top don't matter). Pass the `$WID` from Step 2 as the argument.

```bash
cat > /tmp/space_search.sh << 'EOF'
#!/bin/bash
set -u
WID="$1"
[ -z "$WID" ] && { echo "ERROR: no WID passed"; exit 2; }

blank_check() {
    # Returns 0 (success) if the window now renders content (on active Space).
    cap=$(python3 ~/agentic_os/tools/computer.py screenshot_window "$WID" 2>&1)
    blank=$(echo "$cap" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("blank"))' 2>/dev/null)
    [ "$blank" = "False" ]
}

press_key() {
    if [ "$1" = "right" ]; then code=124; else code=123; fi
    osascript -e "tell application \"System Events\" to key code $code using control down"
}

# Starting Space (Step 2 may have already worked):
if blank_check; then echo "FOUND_AT_START wid=$WID"; exit 0; fi

# Cycle RIGHT
for i in 1 2 3 4 5; do
    press_key right; sleep 1.5
    if blank_check; then echo "FOUND_RIGHT cycle=$i wid=$WID"; exit 0; fi
done

# Reset to start (cycle left the same number)
for j in 1 2 3 4 5; do press_key left; sleep 0.5; done

# Cycle LEFT from start
for i in 1 2 3 4 5; do
    press_key left; sleep 1.5
    if blank_check; then echo "FOUND_LEFT cycle=$i wid=$WID"; exit 0; fi
done

echo "EXHAUSTED — window $WID not on any of 5 right + 5 left Spaces"
exit 1
EOF
chmod +x /tmp/space_search.sh
RESULT=$(bash /tmp/space_search.sh "$WID")
echo "$RESULT"
```

Parse the LAST line of `$RESULT`:
- `FOUND_AT_START`/`FOUND_RIGHT`/`FOUND_LEFT` → the Celonis window now renders on the active Space. Re-run `screenshot_window "$WID"` to confirm `blank: false`, then proceed to Step 3 (reuse the same `$WID`).
- `EXHAUSTED ...` → abort cleanly. Telegram Taran: "Searched 10 Spaces, the Celonis Chrome window (WID $WID) doesn't render on any — it may be minimized or in a fullscreen Space the arrow-shortcuts don't reach. Restart Chrome with the Celonis URL as a fresh window on the current Space." Do NOT Cmd+Q Chrome, do NOT cycle further.

### Step 3 — Click "Ask Gemini" in the toolbar
Use **window-specific OCR** so the search is scoped to the Celonis Chrome window (not full-screen). `find_text_in_window` returns screen-absolute coords ready for `click`.

```bash
# $WID is from Step 2's find_chrome_window_by_url. Reuse it throughout the rest of the steps.
GEM=$(python3 ~/agentic_os/tools/computer.py find_text_in_window "$WID" Gemini)
echo "$GEM"
GX=$(echo "$GEM" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("x") or "")')
GY=$(echo "$GEM" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("y") or "")')
if [ -z "$GX" ]; then
    echo "ABORT: Ask Gemini button not OCR'd in window $WID"
    exit 1
fi
# Before clicking, ensure Chrome is the frontmost app so the click lands on
# Chrome (not on whatever happens to be in front like Terminal):
osascript -e 'tell application "Google Chrome" to activate'
sleep 0.5
python3 ~/agentic_os/tools/computer.py click "$GX" "$GY"
sleep 2
# Verify panel opened by re-capturing the window and inspecting:
python3 ~/agentic_os/tools/computer.py screenshot_window "$WID" ~/agentic_os/screenshots/job_app_${TS}_03_panel.png
```
Read `03_panel.png`. Verify:
- Gemini side panel opened on the right edge of the Chrome window
- Visible greeting ("Hello, Taranveer" / suggestion chips)
- "Sharing: <Celonis title>" line near the bottom (confirms Gemini sees the tab)

If the panel didn't open, retry step 3 once, then abort.

### Step 4 — Click "Start task" (Gemini's agentic activation)
After you send the brief (Step 3 / Step 5), Gemini shows a **"Review your Chrome auto browse plan"** section with a **"Start task"** button (blue, bottom-right of the plan box) and a "Cancel" button to its left. There is NO multi-colored diamond in the 2026 UI.

**OCR note:** "Start task" has white text on a blue background — Tesseract often misses "Start" but finds "task" or "Cancel". Use the Cancel-offset approach as the reliable path:

```bash
# Try OCR for the button text first:
START=$(python3 ~/agentic_os/tools/computer.py find_text_in_window "$WID" "Start task")
SX=$(echo "$START" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("x") or "")')
SY=$(echo "$START" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("y") or "")')

if [ -z "$SX" ]; then
    # White-on-blue OCR miss — anchor off "Cancel" and offset right
    CANCEL=$(python3 ~/agentic_os/tools/computer.py find_text_in_window "$WID" "Cancel")
    CX=$(echo "$CANCEL" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("x") or "")')
    CY=$(echo "$CANCEL" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("y") or "")')
    SX=$((CX + 200)); SY=$CY
fi

if [ -z "$SX" ]; then
    echo "ABORT: neither 'Start task' nor 'Cancel' found — Gemini may have answered conversationally"
    exit 1
fi

osascript -e 'tell application "Google Chrome" to activate'
sleep 0.3
python3 ~/agentic_os/tools/computer.py click "$SX" "$SY"
sleep 4
python3 ~/agentic_os/tools/computer.py screenshot_window "$WID" ~/agentic_os/screenshots/job_app_${TS}_04_started.png
```
Read `04_started.png`. Verify: "Task started" banner appears at the top of the Gemini panel. If not, the click missed — retry once with SX=$((CX + 220)).

### Step 5 — Click chat input, paste brief, send
```bash
# OCR within the window for the chat input placeholder (returns screen coords):
CHAT=$(python3 ~/agentic_os/tools/computer.py find_text_in_window "$WID" Type)
echo "$CHAT"
CX=$(echo "$CHAT" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("x") or "")')
CY=$(echo "$CHAT" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("y") or "")')
if [ -z "$CX" ]; then
    # Fallback: try alternate placeholder text
    CHAT=$(python3 ~/agentic_os/tools/computer.py find_text_in_window "$WID" "Ask Gemini")
    CX=$(echo "$CHAT" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("x") or "")')
    CY=$(echo "$CHAT" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("y") or "")')
fi
osascript -e 'tell application "Google Chrome" to activate'
sleep 0.3
python3 ~/agentic_os/tools/computer.py click "$CX" "$CY"
sleep 0.5
python3 ~/agentic_os/tools/computer.py key cmd+v
sleep 0.5
python3 ~/agentic_os/tools/computer.py key enter
sleep 3
python3 ~/agentic_os/tools/computer.py screenshot_window "$WID" ~/agentic_os/screenshots/job_app_${TS}_05_sent.png
```
Read `05_sent.png`. Verify the brief appears in the chat history (message bubble starts with "You are helping me apply to..."). If empty / partial, the paste failed — re-stage the clipboard (Step 1) and retry once.

### Step 6 — Monitor for 5–8 minutes
Poll every 60 seconds. Use window-specific capture — works even if you switch focus to another app while monitoring.
```bash
for i in $(seq 1 8); do
  sleep 180
  python3 ~/agentic_os/tools/computer.py screenshot_window "$WID" ~/agentic_os/screenshots/job_app_${TS}_poll_${i}.png
  # Read this screenshot. Look for the signals below. Exit early when done.
done
```
Each poll, look for:
- **"Check your task" widget** appears top-right of Chrome → Gemini accepted the agentic task ✅
- **Form fields populating** in the main page area (name, email, etc.) → Gemini is acting ✅
- **Conversational reply only** in the side panel ("I can help you..." with no action) → Gemini did NOT enter agentic mode ❌ abort
- **Side panel disappeared** between polls → auto-collapse bug; re-click "Ask Gemini" toolbar button to reopen, then resume polling

Exit conditions:
- Form fully populated (name + email + phone + at least one textarea filled)
- Gemini reports done in the chat
- 8 minutes elapsed

### Step 7.5 — Submit (ONLY if SUBMIT=true)
**Skip this step entirely if `SUBMIT=false` or unset.** When `SUBMIT=true`:

```bash
# Verify the form is in a submittable state — read the final poll screenshot,
# confirm Name + Email + at least one screening response are visibly filled.
# If anything required looks blank, ABORT submission and Telegram Taran.

# Find the Submit button via window-OCR (returns screen-absolute coords).
# Greenhouse forms use "Submit Application":
SUB=$(python3 ~/agentic_os/tools/computer.py find_text_in_window "$WID" "Submit Application")
SX=$(echo "$SUB" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("x") or "")')
SY=$(echo "$SUB" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("y") or "")')
if [ -z "$SX" ]; then
    SUB=$(python3 ~/agentic_os/tools/computer.py find_text_in_window "$WID" Submit)
    SX=$(echo "$SUB" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("x") or "")')
    SY=$(echo "$SUB" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("y") or "")')
fi
if [ -z "$SX" ]; then
    echo "ABORT: Submit button not OCR'd. Telegram Taran with the latest poll screenshot."
    exit 1
fi
osascript -e 'tell application "Google Chrome" to activate'
sleep 0.3
python3 ~/agentic_os/tools/computer.py click "$SX" "$SY"
sleep 5
TS_SUB=$(date +%s)
python3 ~/agentic_os/tools/computer.py screenshot_window "$WID" ~/agentic_os/screenshots/job_app_${TS_SUB}_07_submitted.png
```
Read `07_submitted.png`. Confirm a success state (e.g. "Thank you for applying", "Application received", "We've received your submission"). If the page shows a validation error instead, abort and Telegram Taran with the error text — do NOT keep clicking.

### Step 7 — Final screenshot and report
```bash
python3 ~/agentic_os/tools/computer.py screenshot_window "$WID" ~/agentic_os/screenshots/job_app_${TS}_final.png
```
Telegram-message Taran a structured report:
```
Job app: {COMPANY} — {ROLE}
Filled: <list of fields with values>
Blank: <list of fields, especially file uploads>
Issues: <any errors, refusals, or stalls>
Status: Form ready for your review. I did NOT click Submit.
Screenshot: ~/agentic_os/screenshots/job_app_${TS}_final.png
```
Also append a one-line entry to the vault log:
```bash
BASE="$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain"
printf "\n## [$(date +%%Y-%%m-%%d)] career | %s — %s — filled %d fields, %d blank\n" \
  "$COMPANY" "$ROLE" "$FILLED_COUNT" "$BLANK_COUNT" >> "$BASE/log.md"
```

---

## Known failure modes (recognise + recover)

| Symptom | Cause | Action |
|---|---|---|
| `browser_fill` returns `status: no_start_task` | OCR missed "Start task" (white text on blue button). `find_chrome_window_by_url` returns `wid: null` because page title contains `\|` (e.g. TheMuse "…\| The Muse") which broke the old pipe-delimited AppleScript output. Both are fixed in the current code as of 2026-05-29. | Verify you're running the latest `tools/computer.py` (uses `\x00` separator + fuzzy title match). For the click, use the Cancel+200px offset path in Step 4. |
| `find_chrome_window_by_url` returns `wid: null` | Page title contains a pipe (`\|`) character, which was breaking the AppleScript output split. Fixed 2026-05-29: separator changed to `\x00`. | Pull latest `tools/computer.py`. |
| TheMuse "Apply on Company Site" needs login | TheMuse gates the external apply URL behind their login wall. Gemini ends up filling the brief against the listing page and may answer conversationally. | Use the direct Greenhouse URL (`https://job-boards.greenhouse.io/<company>/jobs/<id>`) instead of the TheMuse listing URL. Career agent should resolve this before dispatching. |
| `screenshot_window` returns `blank: true` | The target Chrome window is on a different macOS Space — macOS doesn't render pixels for off-Space windows. | Run Step 2.5 (Ctrl+Right/Left Space cycling). Re-run `find_chrome_window_by_url` + `screenshot_window` after each Space change. Abort if 10 cycles exhaust. Do NOT Cmd+Q Chrome. |
| Full-screen `screenshot` shows wallpaper but `screenshot_window` works | Other apps (Terminal, Slack) are on top of Chrome. This is fine — `screenshot_window` and `find_text_in_window` bypass stacking. Continue. | No action — keep using window-specific capture. |
| Step 3: OCR can't find "Gemini" in toolbar | Chrome not frontmost, or button hidden | AppleScript re-raise the window; retry once; then abort |
| Step 4: panel doesn't change after diamond click | Wrong icon clicked (it's not always a diamond — Gemini renames) | Re-read the screenshot; try a different visually-distinct icon near the input row; max 2 retries |
| Step 5: brief doesn't appear in chat | Chat input wasn't focused, OR clipboard cleared by intervening computer.py call | Run `pbpaste | head -c 80` to verify clipboard; re-stage if empty; click chat input again |
| Step 6: only conversational reply | Diamond didn't activate agentic mode | Step 4 failed silently — abort and tell Taran which icon you clicked |
| Side panel disappears between polls | Known auto-collapse bug (focus shifts from screencapture suspected) | Re-click "Ask Gemini" toolbar button to reopen; consider widening poll interval to reduce screencapture frequency |
| Gemini asks for clarification in chat | Brief was ambiguous, OR profile data conflicts with form | Reply once via paste-and-enter using the relevant section of JOB_APP_BRIEF.md; do not invent new info |
| Form field requires upload | OS file picker, browser agent can't drive it | Leave blank, note in the report ("Resume blank — Taran to drag manually") |
| Chrome's "Allow JavaScript from Apple Events" is off | Limits programmatic DOM polling (not blocking — we're vision-driven) | Ignore; vision polling is sufficient |

## What NOT to do

- Do NOT click Submit / Apply Now after the form is filled, **unless `SUBMIT=true` in inputs** — then follow Step 7.5 carefully.
- Do NOT tailor the resume per job. One PDF (`~/agentic_os/resume.pdf`).
- Do NOT fabricate work experience. Stick to the resume facts in `JOB_APP_BRIEF.md`.
- Do NOT close the Chrome window when done — leave the filled form visible.
- Do NOT dispatch to other PAIS agents — this task is self-contained.
