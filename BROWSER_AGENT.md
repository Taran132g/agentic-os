# PAIS — Driving an In-Chrome Browser Agent to Fill Web Forms

Process spec for the PAIS general agent. Read this when asked to fill a web form (job application, sign-up, etc.) inside Taran's real Chrome.

## Which in-Chrome agent to use

| Agent | Where it lives | Quota | Default? |
|---|---|---|---|
| **Gemini in Chrome** | "Ask Gemini" button, top-right of Chrome's toolbar (labeled with text — easy to find via OCR) | Google account / free for most users — **separate from Taran's Claude subscription** | ✅ **Yes — preferred** |
| Claude for Chrome | Orange sunburst icon in the toolbar, directly left of the puzzle-piece menu | Taran's Claude subscription | Fallback |
| ChatGPT Atlas | Standalone app on Taran's Mac | OpenAI account | Alternative |

**Default to Gemini.** It saves Taran's Claude subscription quota entirely on the in-browser side. Only fall back to Claude-for-Chrome if Gemini's "Start task" mode is unavailable or refuses the task.

## How agentic Gemini works (the part that matters)

Gemini-in-Chrome opens as a side panel from the "Ask Gemini" toolbar button. By default it's a **chat** — answers questions about the current page. To get it to **act on the page** (click, fill, navigate), there's a **"Start task"** control inside the side panel. Click that AFTER pasting the brief; Gemini then takes over the tab and executes.

## Prerequisites
- Chrome is running and Taran is signed into his Google account.
- The target page is already loaded in Chrome's active tab.
- `python3` has Accessibility + Screen Recording grants on this Mac (already in place — PAIS has used `tools/computer.py` successfully many times).
- For DOM-polling progress checks: `View → Developer → Allow JavaScript from Apple Events` is enabled in Chrome (otherwise `execute javascript` via AppleScript fails).

---

## Workflow (Gemini, the default path)

Drive everything via `python3 ~/agentic_os/tools/computer.py <cmd>` (Bash). Every step, screenshot + read to confirm before the next.

### 1. Bring Chrome forward AND make sure the right window is on top

If Taran has multiple Chrome profiles open (Penn State + main), `tell application "Google Chrome" to activate` picks *a* window, not necessarily the one with the target tab. Enumerate first:

```bash
python3 ~/agentic_os/tools/computer.py run_applescript 'tell application "Google Chrome"
  set out to ""
  repeat with w from 1 to count of windows
    set out to out & (id of window w) & "|" & (title of active tab of window w) & linefeed
  end repeat
  return out
end tell'
```

Pick the window-id whose active tab title matches your target. Then:

```bash
python3 ~/agentic_os/tools/computer.py run_applescript 'tell application "Google Chrome" to set index of (first window whose id is <ID>) to 1'
```

Screenshot to verify the target page is what's visible.

### 2. Open Gemini's side panel

The "Ask Gemini" button is at the top-right of Chrome's toolbar — it has **visible text** ("Ask Gemini"), so OCR finds it reliably:

```bash
python3 ~/agentic_os/tools/computer.py find_text Gemini
```

This returns screen-point coords (the `x`, `y` keys — they're already pixel-scaled for retina). Click them:

```bash
python3 ~/agentic_os/tools/computer.py click <x> <y>
```

Screenshot to confirm the Gemini side panel opened on the right.

### 3. Click the chat input + paste the brief

The chat input is at the bottom of the side panel — labeled "Ask Gemini" or "Type a message". Find it via vision (Read the screenshot, identify the input box, divide pixel coords by 2 for screen points).

```bash
python3 ~/agentic_os/tools/computer.py click <input_x> <input_y>
```

Paste the brief (`tools/browser_fill.py:build_brief()` generates it; or paste the template below verbatim with the target URL + company + role substituted in):

```bash
python3 ~/agentic_os/tools/computer.py type "<the full brief>"
python3 ~/agentic_os/tools/computer.py key enter
```

Screenshot to confirm the message appears in the chat history.

### 4. Activate agentic mode — click "Start task"

Gemini reads the prompt but won't *act* on the page unless you start a task. Look for a "Start task" button or toggle in the side panel (usually below or near the message). Find via OCR:

```bash
python3 ~/agentic_os/tools/computer.py find_text "Start task"
```

If found, click those coords. If not visible immediately, scroll the side panel up — it may be near the top.

### 5. Monitor

Every 60 seconds (loop 10× for ~10 min):
- Screenshot to `/tmp/state_N.png`, Read it.
- Optionally read the form's DOM state to detect filled fields (only works if "Allow JavaScript from Apple Events" is enabled in Chrome):

```bash
python3 ~/agentic_os/tools/computer.py run_applescript 'tell application "Google Chrome"
  return execute (active tab of window 1) javascript "JSON.stringify({fn:document.querySelector(\"#first_name\")?.value||\"\",em:document.querySelector(\"input[type=email]\")?.value||\"\",ph:document.querySelector(\"input[type=tel]\")?.value||\"\"})"
end tell'
```

Exit the loop when fields are populated OR Gemini reports done OR 10 min elapsed.

### 6. Report

Telegram Taran with: which fields filled, what's blank (especially file uploads), what needs his manual touch. DO NOT click Submit — that's Taran's call.

---

## The application brief (paste as the message)

```text
You are helping me apply to {COMPANY} for the {ROLE} role. The job page is at {URL}. Drive the page yourself — if the form isn't visible, click the "Apply" / "Apply Now" / "Apply on company site" button first to reveal it, then fill it out using the data below. DO NOT click Submit — leave the form filled and open for me to review. If the apply button opens a new tab/popup, follow it.

[CONTACT]
Name: Taranveer Singh
Email: ***REDACTED***
Phone: ***REDACTED***
Location: Royersford, PA 19468, United States
LinkedIn: https://linkedin.com/in/taranveer-singh-605320329
GitHub: https://github.com/Taran132g

[EDUCATION]
School: Pennsylvania State University
Degree: Bachelor of Science
Major: Artificial Intelligence Engineering (Minor in Economics)
Start: August 2024
Graduation: May 2027
GPA: 3.1 (only fill if required)

[WORK AUTHORIZATION]
US Citizen — authorized to work in the US without sponsorship. No sponsorship needed now or in the future.

[DEMOGRAPHICS / EEO — fill them, I'm comfortable disclosing]
Race: Asian | Gender: Male | Veteran: No | Disability: No

[STANDARD ANSWERS]
- How did you hear? → LinkedIn
- Compensation expectations → Competitive market rate ($28–35/hour)
- Outstanding offers? → No
- Willing to relocate? → Yes
- Currently enrolled? → Yes, full-time at Penn State

[RESUME]
The resume PDF is at /Users/taranveersingh/Downloads/Taran_resume (37).pdf — if you can't upload it programmatically (extensions usually can't access OS file dialogs), leave the file-upload blank and tell me to drag it in manually.

[SCREENING QUESTIONS]
For free-text questions, write concise honest answers grounded in resume facts: Penn State AI Engineering student graduating May 2027; Piontrix co-founder; EV Bots intern (Python/Flask/AWS/Firebase IoT data pipelines); Penn State DeFi Club Trading & Technology Lead; built Agentic OS, Arbitrage Trading Bot, FindingFounders. Don't invent experience beyond this. Keep each answer under 120 words.

When done, summarize what fields you filled, what was blank (especially file uploads), and anything that needs my manual attention. Do NOT click Submit.
```

---

## Fallback: Claude for Chrome

If Gemini refuses the task or "Start task" isn't available, use Claude for Chrome:

- The icon is the **orange sunburst** in the toolbar, directly **LEFT of the puzzle-piece** extensions menu. NOT labeled with text — find it visually from a screenshot, not OCR.
- Once clicked, the side panel opens. It's agentic by default (no "Start task" step needed — pasting the brief is enough).
- Costs Taran's Claude subscription quota, so prefer Gemini.

## Recovery / known issues
- **Wrong Chrome window comes forward.** AppleScript `activate` picks one — use the window-enumeration step above to identify and raise the correct one by id.
- **OCR can't find "Claude" text.** That's expected (icon-only button). Use Gemini's "Ask Gemini" button instead — it has visible text.
- **`execute javascript` errors via AppleScript.** Chrome's "Allow JavaScript from Apple Events" is off. Tell Taran to enable it in `View → Developer`.
- **File upload field present.** Browser agents typically can't drive OS file pickers — leave blank, tell Taran to drag the PDF in.
- **Active meeting (Zoom / Meet / Teams).** Proceed gently — do not minimize the meeting window. Use AppleScript to switch Chrome's tab without raising Chrome above the meeting if possible.

## What NOT to do
- Don't tailor the resume per job. One PDF, used everywhere (~/agentic_os/resume.pdf).
- Don't fabricate experience.
- Don't click Submit / Apply Now once the form is filled — review is Taran's job.
- Don't close the browser when done — leave the filled form visible.
