"""
Playwright DOM-driven job-application filler — replaces the blind OCR/coordinate
clicking in tools/browser_fill.py.

Why this exists
---------------
browser_fill.py drove Gemini-in-Chrome by screenshotting the window, OCR'ing it
to guess button locations, then firing OS-level `pyautogui.click(x, y)` + global
keystrokes. On macOS that breaks constantly: Chrome drifts off the active Space,
the hardcoded chat-input guess lands on the Dock (so a click opens a random app),
and keystrokes go to whatever window is focused. There is no page connection.

This tool drives the real browser through Playwright:
  * launches the existing persistent Chromium profile (~/agentic_os/.browser_profile)
    so logged-in sessions/cookies carry over,
  * navigates to the job URL,
  * fills the ENTIRE form off the DOM — text inputs, textareas, native <select>,
    AND react-select comboboxes (Country, work-authorization, EEO, experience
    levels), matching options by typing + clicking the real menu item,
  * answers known fields from PROFILE (deterministic rules) and any arbitrary
    remaining question via a `claude -p` pass over application_profile.md,
  * uploads the résumé, and NEVER submits — the tab is left open for Taran to
    review + submit.

No screen coordinates, no Spaces problem, no Dock misfires, no OCR, no Gemini.
("Ask Gemini" is Chrome browser-UI, not page DOM — Playwright can't and needn't
click it; the filler does the whole job itself.)

PROFILE mirrors application_profile.md (the human source of truth). Keep in sync.

CLI:
    python3 tools/pais_browser.py <job_url>            # visible, fill, keep open
    python3 tools/pais_browser.py <job_url> --headless
    KEEP_OPEN=1800 python3 tools/pais_browser.py <url> # seconds to stay open
    FILL_NO_LLM=1 ...                                  # skip the claude question pass
"""

import os
import re
import sys
import json
import time
import tempfile
import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

PAIS_DIR        = Path(__file__).resolve().parent.parent
PROFILE_DIR     = str(PAIS_DIR / ".browser_profile")
RESUME_PATH     = PAIS_DIR / "resume.pdf"
COVER_LETTER_TXT = PAIS_DIR / "cover_letter.txt"   # source text (with [placeholders])
PROFILE_MD      = PAIS_DIR / "application_profile.md"
SHOT_DIR     = PAIS_DIR / "screenshots"
SHOT_DIR.mkdir(exist_ok=True)

# ── Profile (mirror of application_profile.md) ────────────────────────────────
PROFILE = {
    "first_name": "Taranveer",
    "last_name":  "Singh",
    "full_name":  "Taranveer Singh",
    "email":      "taran.impact@gmail.com",
    "phone":      "484-792-8221",
    "linkedin":   "https://linkedin.com/in/taranveer-singh",
    "github":     "https://github.com/Taran132g",
    "location":   "Royersford, PA",
    "city":       "Royersford",
    "state":      "Pennsylvania",
    "zip":        "19468",
    "country":    "United States",
    "school":     "Pennsylvania State University",
    "degree":     "Bachelor of Science",
    "major":      "Artificial Intelligence Engineering",
    "gpa":        "3.1",
    "grad_year":  "2028",
}

# Short skills hint so the LLM answers experience-level questions in-character
# (grounded in the résumé) rather than guessing.
SKILLS_HINT = (
    "Skill levels: Python — advanced (primary language, trading bots, AI agents). "
    "JavaScript/TypeScript & React — intermediate/advanced. AI-assisted dev tools "
    "(Claude Code, Cursor, Copilot) — advanced (builds with them daily). "
    "Unit testing / testing best practices — intermediate. Go — none/beginner. "
    "Java/Ruby — beginner. ~2 years of practical software development (internships "
    "+ Piontrix + personal projects)."
)

# Two rule sets, checked in this order against the field LABEL:
#  1. QUESTION_RULES — screening questions; may match long sentence labels.
#  2. FIELD_RULES   — profile-identity fields; ONLY applied to SHORT labels, so a
#     word inside a long question ("...United States", "...GitHub Copilot") can't
#     hijack a state/github field rule.
QUESTION_RULES: list[tuple[str, str]] = [
    (r"legally authorized|authorized to work",          "Yes"),
    (r"require sponsor|need sponsor|sponsorship",        "No"),
    (r"willing to (work )?remote|work remotely",         "Yes"),
    (r"willing to relocate|relocat",                     "Yes"),
    (r"how did you hear",                                "LinkedIn"),
    (r"refer(red)? you|did (someone|anyone).*refer",     "No"),
    (r"hispanic|latino",                                 "No"),
    (r"\bgender\b",                                       "Male"),
    (r"\brace\b|ethnicity",                              "Asian"),
    (r"veteran",                                          "not a"),
    (r"disab",                                            "No,"),
]
FIELD_RULES: list[tuple[str, str]] = [
    (r"first\s*name|given name|legal first",            PROFILE["first_name"]),
    (r"last\s*name|family name|surname|legal last",     PROFILE["last_name"]),
    (r"preferred name|full name|^name$|your name",      PROFILE["full_name"]),
    (r"e[-\s]?mail",                                     PROFILE["email"]),
    (r"phone|mobile|telephone|\bcell\b",                PROFILE["phone"]),
    (r"linkedin",                                        PROFILE["linkedin"]),
    (r"\bgithub\b|git hub",                              PROFILE["github"]),
    (r"website|portfolio|personal site",                PROFILE["github"]),
    (r"currently located|current location|^location",   PROFILE["location"]),
    (r"\bcity\b",                                        PROFILE["city"]),
    (r"\bstate\b|province",                              PROFILE["state"]),
    (r"\bzip\b|postal",                                  PROFILE["zip"]),
    (r"\bcountry\b",                                     PROFILE["country"]),
    (r"school|university|college|institution",           PROFILE["school"]),
    (r"\bgpa\b|grade point",                             PROFILE["gpa"]),
]
SHORT_LABEL = 60          # profile-field rules only apply to labels this short


# ── label extraction (runs in the page) ──────────────────────────────────────
_LABEL_JS = r"""
(el) => {
  const txt = (s) => (s || '').replace(/\s+/g, ' ').trim();
  if (el.id) {
    const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    if (l && txt(l.innerText)) return txt(l.innerText);
  }
  let p = el.closest('label');
  if (p && txt(p.innerText)) return txt(p.innerText);
  if (el.getAttribute('aria-label')) return txt(el.getAttribute('aria-label'));
  const lb = el.getAttribute('aria-labelledby');
  if (lb) {
    const parts = lb.split(' ').map(id => {
      const n = document.getElementById(id); return n ? txt(n.innerText) : '';
    }).filter(Boolean);
    if (parts.length) return txt(parts.join(' '));
  }
  const grp = el.closest('div,fieldset,section,li');
  if (grp) {
    const cand = grp.querySelector('label,.label,legend');
    if (cand && txt(cand.innerText)) return txt(cand.innerText);
  }
  return txt(el.getAttribute('placeholder') || el.getAttribute('name') || el.id);
}
"""


def _rule(label: str) -> str | None:
    low = label.lower()
    for pat, val in QUESTION_RULES:
        if re.search(pat, low):
            return val
    if len(label) <= SHORT_LABEL:
        for pat, val in FIELD_RULES:
            if re.search(pat, low):
                return val
    return None


# ── control discovery ────────────────────────────────────────────────────────
def _kind(el) -> str:
    tag = (el.evaluate("e => e.tagName.toLowerCase()") or "")
    if tag == "select":
        return "select"
    if tag == "textarea":
        return "textarea"
    role = el.get_attribute("role") or ""
    typ = (el.get_attribute("type") or "").lower()
    if typ == "file":
        return "file"
    if role == "combobox":
        return "combobox"
    if typ in ("hidden", "submit", "button", "checkbox", "radio"):
        return "skip"
    return "text"


def _extract_controls(page) -> list[dict]:
    """Every fillable control, in document order, with id/kind/label."""
    out = []
    seen = set()
    for el in page.query_selector_all("input, textarea, select"):
        try:
            if not el.is_visible():
                continue
            kind = _kind(el)
            if kind == "skip":
                continue
            eid = el.get_attribute("id") or f"_anon{len(out)}"
            if eid in seen:
                continue
            seen.add(eid)
            label = (el.evaluate(_LABEL_JS) or "").strip()
            out.append({"el": el, "id": eid, "kind": kind, "label": label})
        except Exception:
            continue
    return out


# ── combobox (react-select) handling ─────────────────────────────────────────
CLICK_MS = 8000          # fail a blocked click fast instead of the 30s default


def _dismiss_menus(page) -> None:
    """Close any open react-select menu + blur the active field so its portal
    overlay can't cover (and block measurement of) the next combobox — the cause
    of the 'Element is not visible' cascade."""
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(100)
        page.evaluate("() => document.activeElement && document.activeElement.blur "
                      "&& document.activeElement.blur()")
        page.wait_for_timeout(120)
    except Exception:
        pass


def _open_combo(page, el) -> None:
    """Open a react-select control with a real coordinate mouse-click on its
    VISIBLE wrapper. The inner input[role=combobox] is ~3px wide, so a direct
    element click intermittently reports 'not visible'; clicking the wrapper's
    bounding-box center is a true pointer event react-select always honors."""
    try:
        wrapper = el.evaluate_handle(
            "e => e.closest('.select__control') || e.closest('[class*=control]') "
            "|| e.parentElement").as_element()
    except Exception:
        wrapper = None
    target = wrapper or el
    try:
        target.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        try:
            target.evaluate("n => n.scrollIntoView({block: 'center'})")
        except Exception:
            pass
    box = None
    try:
        box = target.bounding_box()
    except Exception:
        box = None
    if box:
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    else:
        target.click(timeout=CLICK_MS, force=True)


def _combobox_options(page, el, limit: int = 40) -> list[str]:
    """Open a react-select combobox and read its option labels (best-effort)."""
    try:
        _dismiss_menus(page)
        _open_combo(page, el)
        page.wait_for_timeout(350)
        opts = page.query_selector_all('[id*="-option-"]') or page.query_selector_all('[role=option]')
        labels = [(o.inner_text() or "").strip() for o in opts[:limit]]
        labels = [t for t in labels if t]
        _dismiss_menus(page)
        return labels
    except Exception:
        _dismiss_menus(page)
        return []


def _set_combobox(page, el, want: str, results: dict, label: str) -> None:
    """Fill a react-select combobox: open → type to filter the menu → pick the
    option whose text best matches `want` and click it by COORDINATE (a real
    pointer event on the option's box, which survives the ~3px-input and
    portal-overlay quirks that break element.click). Matching the option by text —
    rather than pressing Enter on the highlighted row — means a non-searchable
    select (where typing doesn't filter) still lands the right value."""
    try:
        _dismiss_menus(page)                     # clear any overlay from the prior field
        _open_combo(page, el)
        page.wait_for_timeout(300)
        page.keyboard.type(want[:25], delay=15)  # narrows long lists; harmless otherwise
        page.wait_for_timeout(500)
        opts = page.query_selector_all('[id*="-option-"]') or page.query_selector_all('[role=option]')
        wl = want.strip().lower()
        target = None
        for o in opts:                           # prefer a startswith match
            if (o.inner_text() or "").strip().lower().startswith(wl):
                target = o; break
        if not target:                           # then any substring match
            for o in opts:
                if wl in (o.inner_text() or "").strip().lower():
                    target = o; break
        if not target and opts:                  # else the top filtered option
            target = opts[0]
        if not target:
            _dismiss_menus(page)
            results["skipped"].append(f"{label[:45]} (no option '{want[:18]}')")
            return
        chosen = (target.inner_text() or "").strip()
        box = target.bounding_box()
        if box:
            page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        else:
            target.click(timeout=CLICK_MS, force=True)
        page.wait_for_timeout(200)
        _dismiss_menus(page)
        results["filled"].append(f"[combo] {label[:38]} → {chosen[:28]}")
    except Exception as e:
        _dismiss_menus(page)
        results["errors"].append(f"combo {label[:25]}: {str(e)[:50]}")


def _set_text(el, want: str, results: dict, label: str) -> None:
    try:
        el.fill(want)
        results["filled"].append(f"{label[:40]} → {want[:32]}")
    except Exception as e:
        results["errors"].append(f"text {label[:25]}: {str(e)[:40]}")


def _set_native_select(el, want: str, results: dict, label: str) -> None:
    try:
        for o in el.query_selector_all("option"):
            if want.lower() in (o.inner_text() or "").strip().lower():
                el.select_option(value=o.get_attribute("value"))
                results["filled"].append(f"[select] {label[:35]} → {want[:25]}")
                return
        results["skipped"].append(f"{label[:45]} (no option ~ '{want[:20]}')")
    except Exception as e:
        results["errors"].append(f"select {label[:25]}: {str(e)[:40]}")


# ── arbitrary questions → claude -p (Taran's subscription) ────────────────────
def _claude_answers(unknowns: list[dict]) -> dict:
    """Map arbitrary remaining fields to answers via one `claude -p` JSON call,
    grounded in application_profile.md + the skills hint. Combobox fields include
    their option list so the model returns an EXACT option. Returns {id: answer},
    where answer 'SKIP' (or missing) means leave the field for Taran."""
    if os.environ.get("FILL_NO_LLM") == "1" or not unknowns:
        return {}
    try:
        profile_txt = PROFILE_MD.read_text()[:6000]
    except Exception:
        profile_txt = ""
    spec = [{"id": u["id"], "label": u["label"], "kind": u["kind"],
             **({"options": u["options"]} if u.get("options") else {})}
            for u in unknowns]
    prompt = (
        "You are filling a job application for Taranveer Singh. Use his profile + "
        "skills below to answer each form field. Return ONLY strict JSON: an object "
        "mapping each field id to the answer string.\n"
        "Rules:\n"
        "- For kind 'combobox' or 'select', the answer MUST be EXACTLY one of the "
        "provided options (verbatim).\n"
        "- For short text, answer concisely (a phrase or number).\n"
        "- For open/essay questions, write a brief, genuine first-person answer "
        "(1-2 sentences) consistent with his profile.\n"
        "- If a field asks for something not in the profile and you cannot answer "
        "honestly (e.g. personal specifics), use \"SKIP\".\n"
        "- Never invent credentials, addresses, or contact info not given.\n\n"
        f"=== PROFILE ===\n{profile_txt}\n\n=== SKILLS ===\n{SKILLS_HINT}\n\n"
        f"=== FIELDS ===\n{json.dumps(spec, ensure_ascii=False)}\n\n"
        "JSON only:"
    )
    try:
        proc = subprocess.run(["claude", "-p", prompt],
                              capture_output=True, text=True, timeout=60)
        raw = (proc.stdout or "").strip()
        raw = re.sub(r"```(?:json)?|```", "", raw)
        m = re.search(r"\{.*\}", raw, re.S)
        return json.loads(m.group(0)) if m else {}
    except Exception as e:
        log.warning("[pais_browser] claude answer pass failed: %s", e)
        return {}


# ── upload ────────────────────────────────────────────────────────────────────
def _upload_resume(page, results: dict) -> None:
    if not RESUME_PATH.exists():
        return
    for el in page.query_selector_all("input[type='file']"):
        try:
            fid = (el.get_attribute("id") or el.get_attribute("name") or "").lower()
            if "cover" in fid:                  # don't put the résumé in cover-letter slots
                continue
            el.set_input_files(str(RESUME_PATH.resolve()))
            results["filled"].append("[file] résumé uploaded")
        except Exception as e:
            results["errors"].append(f"file: {str(e)[:40]}")


def _claude_fill_cover(txt: str, company: str, role: str) -> str:
    """Have claude fill ONLY the [bracketed] placeholders in the cover letter, using
    the job + application profile, leaving every other word exactly as written. This
    keeps Taran's letter (his voice, his template) and just personalizes the brackets
    — catching ALL of them, not only the few hard-coded tokens. '' on any failure."""
    if os.environ.get("FILL_NO_LLM") == "1":
        return ""
    try:
        profile_txt = PROFILE_MD.read_text(errors="ignore")[:5000]
    except Exception:
        profile_txt = ""
    prompt = (
        "Below is Taranveer Singh's cover letter template with [bracketed] placeholders. "
        "Fill in EVERY bracketed placeholder so the letter reads naturally and specifically "
        "for this job. STRICT rules:\n"
        "- Do NOT change, add, or remove any other words — keep every non-placeholder "
        "sentence exactly as written, in his voice.\n"
        "- Remove the square brackets in your output.\n"
        "- Use the job + profile below; never invent credentials, addresses, or contact "
        "info. If a placeholder has no honest value (e.g. a company address you don't "
        "know), drop it cleanly rather than guessing.\n\n"
        f"JOB: {role or '(unspecified role)'} at {company or '(unspecified company)'}\n\n"
        f"=== APPLICANT PROFILE ===\n{profile_txt}\n\n"
        f"=== COVER LETTER TEMPLATE ===\n{txt}\n\n"
        "Output ONLY the finished cover letter text — no preamble, no commentary."
    )
    try:
        proc = subprocess.run(["claude", "-p", prompt],
                              capture_output=True, text=True, timeout=60)
        out = (proc.stdout or "").strip()
        return out if proc.returncode == 0 and out else ""
    except Exception as e:
        log.warning("[pais_browser] claude cover-letter fill failed: %s", e)
        return ""


_COVER_CACHE: dict = {}


def _cover_letter_text(job: dict) -> str:
    """The staged cover letter with its placeholders filled in for THIS job. Claude
    personalizes the [brackets] (kept verbatim otherwise); falls back to a literal
    substitution of the known tokens, then the raw template. Cached per job so the
    file-upload and textarea paths share one claude call. '' when none is staged."""
    key = (job.get("url", ""), job.get("company", ""), job.get("role", ""))
    if key in _COVER_CACHE:
        return _COVER_CACHE[key]
    try:
        txt = COVER_LETTER_TXT.read_text(errors="ignore").strip()
    except Exception:
        txt = ""
    if not txt:
        _COVER_CACHE[key] = ""
        return ""
    company, role = (job.get("company") or "").strip(), (job.get("role") or "").strip()
    result = ""
    if "[" in txt:                              # has placeholders → let claude fill them
        result = _claude_fill_cover(txt, company, role)
    if not result:                             # claude off/failed, or nothing to fill
        result = txt
        if company:
            result = result.replace("[Company Name]", company).replace("[Company Address]", "").replace("[Company]", company)
        if role:
            result = result.replace("[Position Title]", role).replace("[Position]", role).replace("[Role]", role)
    result = result.strip()
    _COVER_CACHE[key] = result
    return result


def _cover_letter_file(job: dict) -> Path | None:
    """A per-job cover-letter PDF (placeholders filled) for file-upload fields,
    generated from the personalized text via cupsfilter. Falls back to the staged
    .docx/.pdf, then None."""
    text = _cover_letter_text(job)
    if text:
        try:
            base = Path(tempfile.gettempdir()) / f"cover_{os.getpid()}_{abs(hash(job.get('url','')))%10000}"
            base.with_suffix(".txt").write_text(text)
            r = subprocess.run(["/usr/sbin/cupsfilter", str(base.with_suffix(".txt"))],
                               capture_output=True, timeout=30)
            if r.returncode == 0 and r.stdout:
                pdf = base.with_suffix(".pdf")
                pdf.write_bytes(r.stdout)
                return pdf
        except Exception:
            pass
    for ext in (".pdf", ".docx", ".doc"):
        p = PAIS_DIR / f"cover_letter{ext}"
        if p.exists():
            return p
    return None


def _upload_cover_letter(page, job: dict, results: dict) -> None:
    """Upload the cover letter into any file field that asks for one — the cover slots
    _upload_resume deliberately skips. No-op when the form has no cover-letter upload
    or nothing is staged."""
    clf = _cover_letter_file(job)
    if not clf:
        return
    for el in page.query_selector_all("input[type='file']"):
        try:
            fid = (el.get_attribute("id") or el.get_attribute("name") or "").lower()
            if "cover" not in fid:              # only the cover-letter file slots
                continue
            el.set_input_files(str(clf.resolve()))
            results["filled"].append("[file] cover letter uploaded")
        except Exception as e:
            results["errors"].append(f"cover-file: {str(e)[:40]}")


# ── the full-form fill ────────────────────────────────────────────────────────
def _apply(page, c: dict, want: str, results: dict) -> None:
    el, kind, label = c["el"], c["kind"], c["label"]
    if kind == "combobox":
        _set_combobox(page, el, want, results, label)
    elif kind == "select":
        _set_native_select(el, want, results, label)
    else:
        _set_text(el, want, results, label)


def _fill_form(page, job: dict, results: dict) -> None:
    """Fill the whole form. Re-scans up to MAX_PASSES times because answering an
    EEO/screening field can REVEAL a follow-up (e.g. selecting Hispanic/Latino=No
    surfaces the conditional 'race' question). Each pass handles only fields not
    yet touched, so conditionally-revealed fields get filled on the next sweep."""
    MAX_PASSES = 3
    handled: set[str] = set()
    for pass_no in range(MAX_PASSES):
        fresh = [c for c in _extract_controls(page) if c["id"] not in handled]
        if not fresh:
            break
        unknowns: list[dict] = []
        for c in fresh:
            handled.add(c["id"])
            el, kind, label = c["el"], c["kind"], c["label"]
            if kind == "file":
                continue                        # handled by _upload_resume
            try:
                if kind in ("text", "textarea") and (el.input_value() or "").strip():
                    continue                    # already populated — don't clobber
            except Exception:
                pass
            if not label:
                continue
            # Cover-letter textarea (paste the real letter, personalized) rather than
            # leaving it blank or letting the generic claude pass invent one.
            if kind in ("text", "textarea") and "cover letter" in label.lower():
                cl = _cover_letter_text(job)
                if cl:
                    _set_text(el, cl, results, label)
                    continue
            want = _rule(label)
            if want is None:
                # No rule — read options now so the LLM can pick an exact one.
                if kind in ("combobox", "select"):
                    c["options"] = (_combobox_options(page, el) if kind == "combobox"
                                    else [(_o.inner_text() or "").strip()
                                          for _o in el.query_selector_all("option")][:40])
                unknowns.append(c)
                continue
            _apply(page, c, want, results)

        if pass_no == 0:
            _upload_resume(page, results)
            _upload_cover_letter(page, job, results)

        # Arbitrary remaining questions via claude (no-ops when unknowns is empty,
        # which is the usual case on re-scan passes).
        answers = _claude_answers(unknowns)
        for c in unknowns:
            ans = answers.get(c["id"])
            if not ans or str(ans).strip().upper() == "SKIP":
                results["skipped"].append(c["label"][:50])
                continue
            _apply(page, c, str(ans), results)

        page.wait_for_timeout(700)              # let any conditional fields render


# ── public entry points ───────────────────────────────────────────────────────
def _clear_stale_singleton(profile_dir: str) -> None:
    """Chromium guards a profile with a SingletonLock symlink → 'HOST-PID'. If that
    PID is DEAD (a crashed/killed prior browser), the lock is stale and every launch
    fails with 'Failed to create a ProcessSingleton'. Clear stale locks so the fill
    can proceed; a LIVE lock (a real browser still on this profile) is left alone."""
    prof = Path(profile_dir)
    try:
        target = os.readlink(prof / "SingletonLock")
    except OSError:
        return                                  # no lock → nothing to clear
    tail = target.rsplit("-", 1)[-1] if "-" in target else ""
    pid = int(tail) if tail.isdigit() else None
    if pid:
        try:
            os.kill(pid, 0)                      # signal 0 = liveness probe
            return                               # alive → real browser, leave it
        except ProcessLookupError:
            pass                                 # dead → stale, clear below
        except PermissionError:
            return                               # exists, not ours → treat as live
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            (prof / name).unlink()
        except OSError:
            pass
    log.info("[pais_browser] cleared stale Chromium singleton (dead pid %s)", pid)


def _click_apply(page):
    """Some postings show a job DESCRIPTION with an 'Apply' button that reveals the
    form inline, navigates to it, or opens it in a new tab. When no form is present
    yet, click the first visible Apply control and wait for the form to appear.

    Returns the page to fill on — the same page, or the new tab if Apply opened one.
    Best-effort and never raises; ordered specific→generic so 'Apply for this job'
    wins over a bare 'Apply' that might be a menu link."""
    selectors = (
        "a:has-text('Apply for this job')", "button:has-text('Apply for this job')",
        "a:has-text('Apply now')", "button:has-text('Apply now')",
        "a:has-text('Apply')", "button:has-text('Apply')",
        "text=/^\\s*apply\\b/i",
    )
    ctx = getattr(page, "context", None)
    for sel in selectors:
        try:
            btn = page.query_selector(sel)
            if not (btn and btn.is_visible()):
                continue
            before = len(ctx.pages) if ctx else 0
            btn.click(timeout=CLICK_MS, force=True)
            page.wait_for_timeout(1500)
            if ctx and len(ctx.pages) > before:          # Apply opened a new tab
                page = ctx.pages[-1]
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=8000)
                except Exception:
                    pass
            try:                                         # let the form render / load
                page.wait_for_selector("input, textarea, select", timeout=6000)
            except Exception:
                page.wait_for_timeout(1500)
            return page
        except Exception:
            continue
    return page


def _fill_job_on_page(page, job: dict) -> dict:
    """Navigate one job on the given page and fill its form. Returns one result dict
    and NEVER raises — failures come back as {ok: False, ...} so a batch or queue
    keeps moving to the next job instead of aborting the whole run."""
    url = (job.get("url") or "").strip()
    job_id = str(job.get("id", "job"))
    results = {"filled": [], "skipped": [], "errors": []}
    shot = str(SHOT_DIR / f"{job_id}_pw.png")
    if not url.startswith("http"):
        return {"ok": False, "status": "error", "error": "no URL", "url": url, **results}
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3500)
        # Description page with an 'Apply' button instead of a form? Click through
        # to the form (may reveal inline, navigate, or open a new tab → fill there).
        if len(page.query_selector_all("input, textarea, select")) == 0:
            page = _click_apply(page)
        if len(page.query_selector_all("input, textarea, select")) == 0:
            page.screenshot(path=shot, full_page=False)
            return {"ok": False, "status": "no_form", "url": url,
                    "error": "no form fields (login/multi-step ATS)",
                    "screenshot": shot, **results}
        _fill_form(page, job, results)
        page.screenshot(path=shot, full_page=False)
        return {"ok": bool(results["filled"]),
                "status": "filled" if results["filled"] else "no_form",
                "url": url, "screenshot": shot, **results}
    except Exception as e:
        return {"ok": False, "status": "error", "url": url, "error": str(e), **results}


def browser_fill_pw(job: dict, headless: bool = False,
                    keep_open_seconds: int = 1800) -> dict:
    """Open the job URL in the persistent Chromium profile, fill the whole form via
    the DOM, upload the résumé, and leave the window open for review. Never submits.

    Returns {ok, status, filled, skipped, errors, screenshot, url}."""
    from playwright.sync_api import sync_playwright

    url = (job.get("url") or "").strip()
    job_id = str(job.get("id", "job"))
    if not url.startswith("http"):
        return {"ok": False, "status": "error", "error": "no application URL"}

    results = {"filled": [], "skipped": [], "errors": []}
    shot = str(SHOT_DIR / f"{job_id}_pw.png")

    _clear_stale_singleton(PROFILE_DIR)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE_DIR, headless=headless,
            viewport={"width": 1280, "height": 900},
            args=["--no-first-run", "--no-default-browser-check"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            ctx.close()
            return {"ok": False, "status": "error", "error": f"navigation failed: {e}", "url": url}
        page.wait_for_timeout(3500)

        # Description page with an 'Apply' button instead of a form? Click through.
        if len(page.query_selector_all("input, textarea, select")) == 0:
            page = _click_apply(page)

        if len(page.query_selector_all("input, textarea, select")) == 0:
            page.screenshot(path=shot, full_page=False)
            _stay_open(ctx, keep_open_seconds)
            return {"ok": False, "status": "no_form", "url": url, "screenshot": shot,
                    "error": "No form fields — this ATS likely needs login "
                             "(Workday/iCIMS) or a multi-step apply flow.", **results}

        _fill_form(page, job, results)
        page.screenshot(path=shot, full_page=False)
        status = "filled" if results["filled"] else "no_form"
        out = {"ok": bool(results["filled"]), "status": status, "screenshot": shot,
               "url": url, **results}
        log.info("[pais_browser] %s — filled %d, skipped %d, errors %d",
                 job.get("company", "?"), len(results["filled"]),
                 len(results["skipped"]), len(results["errors"]))
        _stay_open(ctx, keep_open_seconds)
        return out


def browser_fill_pw_batch(jobs: list[dict], headless: bool = False,
                          keep_open_seconds: int = 1800,
                          after_fill=None) -> list[dict]:
    """Fill several jobs in ONE persistent browser: each opens in its own tab, gets
    fully filled via the DOM, and ALL tabs are left open together for review +
    submit. Returns one result per job.

    `after_fill(results)` (optional) runs once all tabs are filled but BEFORE the
    keep-open block — the caller posts its summary + updates the queue immediately
    while the browser stays up. Hook errors are swallowed so they can't take the
    open window down."""
    from playwright.sync_api import sync_playwright

    out: list[dict] = []
    _clear_stale_singleton(PROFILE_DIR)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE_DIR, headless=headless,
            viewport={"width": 1280, "height": 900},
            args=["--no-first-run", "--no-default-browser-check"])
        for i, job in enumerate(jobs):
            page = ctx.pages[0] if (i == 0 and ctx.pages) else ctx.new_page()
            out.append(_fill_job_on_page(page, job))
        if after_fill:
            try:
                after_fill(out)
            except Exception:
                log.exception("[pais_browser] after_fill hook failed")
        _stay_open(ctx, keep_open_seconds)
    return out


def _stay_open(ctx, seconds: int) -> None:
    """Keep the window open so Taran can review + submit. Closes after `seconds`
    (or immediately if seconds<=0). Interruptible."""
    if seconds <= 0:
        ctx.close(); return
    try:
        time.sleep(seconds)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            ctx.close()
        except Exception:
            pass


def browser_fill_queue(pop_next, on_result, headless: bool = False,
                       idle_review_seconds: int = 600, poll_seconds: float = 2.0) -> int:
    """Drain a DYNAMIC job queue through ONE persistent browser.

    `pop_next()` returns the next job dict (or None when the queue is momentarily
    empty); `on_result(job, result)` is called after each fill. The browser opens
    once (stale singleton cleared first), fills jobs as they arrive — so a second
    'Fill' pressed while this runs is picked up in the SAME window rather than racing
    a new Chromium (no ProcessSingleton) — and once the queue stays empty for
    `idle_review_seconds` it closes. That idle window is the review/submit time; a job
    queued during it resets the window. Returns the count of jobs filled."""
    from playwright.sync_api import sync_playwright
    _clear_stale_singleton(PROFILE_DIR)
    filled = 0
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE_DIR, headless=headless,
            viewport={"width": 1280, "height": 900},
            args=["--no-first-run", "--no-default-browser-check"])
        first = True
        idle_deadline = time.time() + max(0, idle_review_seconds)
        try:
            while True:
                job = pop_next()
                if job is None:
                    if time.time() >= idle_deadline:
                        break
                    time.sleep(poll_seconds)
                    continue
                page = ctx.pages[0] if (first and ctx.pages) else ctx.new_page()
                first = False
                res = _fill_job_on_page(page, job)
                filled += 1
                try:
                    on_result(job, res)
                except Exception:
                    log.exception("[pais_browser] on_result hook failed")
                idle_deadline = time.time() + max(0, idle_review_seconds)
        finally:
            try:
                ctx.close()
            except Exception:
                pass
    return filled


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cli = [a for a in sys.argv[1:] if not a.startswith("--")]
    headless = "--headless" in sys.argv
    keep = int(os.environ.get("KEEP_OPEN", "1800"))
    url = cli[0] if cli else "https://job-boards.greenhouse.io/remesh/jobs/8450776002"
    res = browser_fill_pw({"id": "smoke", "company": "Test", "role": "Intern", "url": url},
                          headless=headless, keep_open_seconds=keep)
    print(json.dumps({k: v for k, v in res.items() if k != "el"}, indent=2))
