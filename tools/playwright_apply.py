"""
Playwright-based job application filler.
Platform-aware: detects Greenhouse, Workday, Lever, or falls back to generic CSS selectors.
Opens the job URL in headed Chromium, fills common form fields,
takes a screenshot, and leaves the browser open for Taran to review and submit.
"""

import base64
import json as _json
import logging
import re
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Persistent Chromium profile — same dir tools/pais_browser.py uses, so any
# service Taran has bootstrap-logged-into stays authenticated for auto-apply.
PROFILE_DIR = Path(__file__).parent.parent / ".browser_profile"

# Vault resume — read once when LLM screening answers are generated, so the
# answers stay grounded in Taran's actual experience.
VAULT_RESUME = (
    Path.home()
    / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain"
    / "About Taran/Resume.md"
)

CONTACT = {
    "first_name":      "Taranveer",
    "last_name":       "Singh",
    "full_name":       "Taranveer Singh",
    "email":           "taran.impact@gmail.com",
    "phone":           "484-792-8221",
    "phone_digits":    "4847928221",
    "phone_formatted": "(484) 792-8221",
    "location":        "Royersford, PA",
    "city":            "Royersford",
    "state":           "PA",
    "zip":             "19468",
    "country":         "United States",
    "university":      "Pennsylvania State University",
    "major":           "Artificial Intelligence Engineering",
    "degree":          "Bachelor of Science",
    "gpa":             "3.1",
    "grad_year":       "2028",
    "grad_date":       "May 2028",
    "start_year":      "2024",
    "linkedin":        "https://linkedin.com/in/taranveer-singh",
    "github":          "https://github.com/Taran132g",
}

# Generic CSS field map (fallback for unknown ATSs)
FIELD_MAP = {
    "first_name": [
        'input[autocomplete="given-name"]', 'input[name="first_name"]', 'input[name="firstName"]',
        'input[id*="firstName" i]', 'input[id*="first_name" i]',
        'input[placeholder*="First name" i]', 'input[aria-label*="First name" i]',
    ],
    "last_name": [
        'input[autocomplete="family-name"]', 'input[name="last_name"]', 'input[name="lastName"]',
        'input[id*="lastName" i]', 'input[id*="last_name" i]',
        'input[placeholder*="Last name" i]', 'input[aria-label*="Last name" i]',
    ],
    "full_name": [
        'input[autocomplete="name"]', 'input[name="name"]', 'input[id*="full_name" i]',
        'input[placeholder*="Full name" i]', 'input[aria-label*="Full name" i]',
    ],
    "email": [
        'input[type="email"]', 'input[autocomplete="email"]', 'input[name="email"]',
        'input[id*="email" i]', 'input[placeholder*="Email" i]',
    ],
    "phone": [
        'input[type="tel"]', 'input[autocomplete="tel"]', 'input[name*="phone" i]',
        'input[id*="phone" i]', 'input[placeholder*="Phone" i]', 'input[aria-label*="Phone" i]',
    ],
    "location": [
        'input[name*="location" i]', 'input[id*="location" i]', 'input[placeholder*="Location" i]',
        'input[aria-label*="Location" i]', 'input[placeholder*="City, State" i]',
    ],
    "city":  ['input[name*="city" i]',  'input[id*="city" i]',  'input[placeholder*="City" i]'],
    "state": ['input[name*="state" i]', 'input[id*="state" i]', 'input[placeholder*="State" i]'],
    "zip":   ['input[name*="zip" i]', 'input[name*="postal" i]', 'input[id*="zip" i]', 'input[placeholder*="Zip" i]'],
    "university": [
        'input[name*="school" i]', 'input[name*="university" i]',
        'input[id*="school" i]', 'input[id*="university" i]',
        'input[placeholder*="University" i]', 'input[placeholder*="School" i]',
        'input[aria-label*="School" i]', 'input[aria-label*="University" i]',
    ],
    "major": [
        'input[name*="major" i]', 'input[name*="field_of_study" i]',
        'input[placeholder*="Major" i]', 'input[placeholder*="Field of study" i]',
    ],
    "grad_year": [
        'input[name*="graduation" i]', 'input[id*="graduation" i]',
        'input[placeholder*="Graduation year" i]',
    ],
    "linkedin": [
        'input[name*="linkedin" i]', 'input[id*="linkedin" i]',
        'input[placeholder*="LinkedIn" i]', 'input[aria-label*="LinkedIn" i]',
    ],
    "github": [
        'input[name*="github" i]', 'input[id*="github" i]',
        'input[placeholder*="GitHub" i]', 'input[aria-label*="GitHub" i]',
    ],
    "portfolio": [
        'input[name*="portfolio" i]', 'input[name*="website" i]',
        'input[id*="portfolio" i]', 'input[placeholder*="Portfolio" i]',
        'input[placeholder*="Website" i]',
    ],
}

# The single review browser currently left open. The persistent profile can
# only be driven by one context at a time, so we close the previous one
# before opening another — this also stops windows from piling up.
#
# Stored as {"pw": <Playwright>, "ctx": <BrowserContext>} so we can shut down
# the playwright instance too. Each fill_application call runs on whatever
# worker thread asyncio.to_thread assigns it, and a Playwright object created
# in thread A cannot be driven from thread B (greenlet.error). So we make a
# fresh `sync_playwright().start()` per call rather than caching globally.
_active: dict | None = None


def _close_active() -> None:
    """Close any previously-open review browser + playwright instance."""
    global _active
    if _active is None:
        return
    try:
        _active["ctx"].close()
    except Exception:
        pass
    try:
        _active["pw"].stop()
    except Exception:
        pass
    _active = None


# ── Platform detection ────────────────────────────────────────────────────────

def _detect_platform(url: str) -> str:
    u = url.lower()
    if "greenhouse.io" in u:
        return "greenhouse"
    if "myworkdayjobs.com" in u or ("workday.com" in u and "/job/" in u):
        return "workday"
    if "lever.co" in u:
        return "lever"
    if "ashbyhq.com" in u:
        return "ashby"
    if "smartrecruiters.com" in u:
        return "smartrecruiters"
    return "generic"


# ── Shared helpers ────────────────────────────────────────────────────────────

def _try_fill(page, selectors: list, value: str, field_key: str, fields_filled: list) -> bool:
    for selector in selectors:
        try:
            el = page.locator(selector).first
            if el.is_visible(timeout=600):
                el.fill(value)   # fill() clears any existing content itself
                fields_filled.append({"field": field_key, "value": value})
                return True
        except Exception:
            continue
    return False


def _try_select(page, selector: str, value: str, field_key: str, fields_filled: list) -> bool:
    try:
        el = page.locator(selector).first
        if el.is_visible(timeout=600):
            el.select_option(label=value)
            fields_filled.append({"field": field_key, "value": value})
            return True
    except Exception:
        pass
    return False


def _wait_for_form(page, timeout_ms: int = 15000) -> bool:
    """Wait for an application form to actually render.

    ATS pages — especially React-based Greenhouse job-boards — fire
    domcontentloaded long before the form inputs mount (the form is rendered
    after an API call resolves the job's questions). Without this wait the
    contact-field loop runs against an empty DOM and fills nothing.

    Returns True if a form field appeared, False on timeout (fillers still
    run best-effort)."""
    try:
        page.wait_for_selector(
            'input#first_name, input[name="first_name"], input[name="firstName"], '
            'input[type="email"], input[name="email"], input[name="name"], '
            'input[type="file"], input[id="_systemfield_name"], textarea',
            timeout=timeout_ms, state="visible",
        )
        # Give the rest of the form a beat to finish mounting.
        page.wait_for_timeout(800)
        return True
    except Exception:
        log.debug("_wait_for_form: no form field appeared within %dms", timeout_ms)
        return False


def _click_apply(page) -> bool:
    for sel in [
        'a:has-text("Apply Now")', 'button:has-text("Apply Now")',
        'a:has-text("Apply")',     'button:has-text("Apply")',
    ]:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=800):
                el.click()
                page.wait_for_timeout(2000)
                return True
        except Exception:
            continue
    return False


def _upload_resume(page, fields_filled: list, resume_path: str | None = None):
    """Upload the resume to a file input. Prefers the per-job tailored PDF
    (resume_path); falls back to the generic resume.pdf."""
    pdf = Path(resume_path) if resume_path else (Path(__file__).parent.parent / "resume.pdf")
    if not pdf.exists():
        log.info("No resume PDF found — skipping file upload")
        return
    try:
        file_input = page.locator('input[type="file"]').first
        if file_input.count() > 0:
            file_input.set_input_files(str(pdf))
            fields_filled.append({"field": "resume", "value": pdf.name})
            page.wait_for_timeout(1500)
            log.info("Uploaded %s", pdf.name)
    except Exception as e:
        log.debug("Resume upload skipped: %s", e)


# ── Cover-letter / EEO / screening-question helpers ───────────────────────────

COVER_SELECTORS = [
    'textarea[name*="cover" i]',
    'textarea[id*="cover" i]',
    'textarea[aria-label*="cover" i]',
    'textarea[placeholder*="cover" i]',
    'textarea[name*="letter" i]',
    'textarea[id*="letter" i]',
    'textarea[aria-label*="letter" i]',
    'textarea[placeholder*="letter" i]',
    'textarea[name*="why" i]',
]

# EEO sections live in selects/radios labelled with these keywords.
EEO_KEYWORDS = (
    "gender", "race", "ethnicity", "veteran", "disability",
    "hispanic", "latino", "sexual orientation", "transgender",
    "pronoun",
)
DECLINE_PHRASES = (
    "decline to self-identify", "decline to self identify",
    "prefer not to answer", "prefer not to say",
    "i do not wish to answer", "i don't wish to answer",
    "decline to answer", "decline to specify",
    "do not wish to disclose", "i don't want to answer",
    "choose not to disclose", "not specified",
)


def _fill_cover_letter(page, hook: str, fields_filled: list):
    """If a cover-letter textarea is present, paste the tailored hook into it."""
    if not hook or not hook.strip():
        return
    for sel in COVER_SELECTORS:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=400):
                el.fill(hook)
                fields_filled.append({"field": "cover_letter", "value": hook[:90]})
                log.info("Cover-letter hook typed into %s", sel)
                return
        except Exception:
            continue


def _fill_eeo_decline(page, fields_filled: list):
    """Find EEO selects/radios and choose a 'decline to self-identify' option.

    Taran's default is to decline disclosure across the board — these fields
    block submit but never gate the application itself."""
    try:
        # ── Selects (gender / race / veteran / disability dropdowns) ──
        select_info = page.evaluate(
            """
            () => {
              const out = [];
              document.querySelectorAll('select').forEach((s, idx) => {
                const id = s.id || '';
                const name = s.name || '';
                const aria = s.getAttribute('aria-label') || '';
                let labelTxt = '';
                if (id) {
                  const l = document.querySelector(`label[for="${CSS.escape(id)}"]`);
                  if (l) labelTxt = (l.innerText||'').trim();
                }
                const context = (labelTxt + ' ' + aria + ' ' + id + ' ' + name).toLowerCase();
                const opts = Array.from(s.options).map(o => o.text || o.value);
                out.push({idx, context, opts, id, name});
              });
              return out;
            }
            """
        ) or []

        for info in select_info:
            if not any(k in info["context"] for k in EEO_KEYWORDS):
                continue
            chosen = None
            for opt in info["opts"]:
                lo = (opt or "").lower()
                if any(p in lo for p in DECLINE_PHRASES):
                    chosen = opt
                    break
            if not chosen:
                continue
            sel = (
                f'select#{info["id"]}' if info.get("id") else
                f'select[name="{info["name"]}"]' if info.get("name") else
                f'select >> nth={info["idx"]}'
            )
            try:
                page.locator(sel).first.select_option(label=chosen)
                fields_filled.append({"field": "eeo_decline", "value": chosen[:60]})
            except Exception:
                # Try selecting by value if label didn't match exactly
                try:
                    page.locator(sel).first.select_option(value=chosen)
                    fields_filled.append({"field": "eeo_decline", "value": chosen[:60]})
                except Exception:
                    pass

        # ── Radio groups (decline-to-self-identify radios) ──
        # Find radios whose label/value matches a decline phrase AND whose
        # surrounding fieldset mentions an EEO keyword.
        radio_info = page.evaluate(
            """
            () => {
              const out = [];
              document.querySelectorAll('input[type="radio"]').forEach((r, idx) => {
                const lbl = r.id ? (document.querySelector(`label[for="${CSS.escape(r.id)}"]`)?.innerText||'') : '';
                const parentLbl = r.closest('label')?.innerText || '';
                const txt = (lbl + ' ' + parentLbl + ' ' + (r.value||'')).toLowerCase();
                const fs = r.closest('fieldset, [role="radiogroup"], .field, [class*="question" i], [class*="form-group" i]');
                const fsTxt = (fs ? fs.innerText : '').slice(0, 600).toLowerCase();
                out.push({idx, txt, fsTxt, id: r.id, name: r.name, value: r.value});
              });
              return out;
            }
            """
        ) or []

        # Group by name so we pick only one radio per question
        chosen_names = set()
        for info in radio_info:
            name = info.get("name") or ""
            if name in chosen_names:
                continue
            if not any(k in info["fsTxt"] for k in EEO_KEYWORDS):
                continue
            if not any(p in info["txt"] for p in DECLINE_PHRASES):
                continue
            sel = (
                f'input[type="radio"]#{info["id"]}' if info.get("id") else
                f'input[type="radio"][name="{name}"][value="{info["value"]}"]'
            )
            try:
                page.locator(sel).first.check(force=True)
                fields_filled.append({"field": f"eeo_radio:{name[:30]}", "value": "decline"})
                chosen_names.add(name)
            except Exception:
                pass
    except Exception as e:
        log.debug("EEO fill skipped: %s", e)


def _detect_screening_questions(page) -> list[dict]:
    """Return unfilled custom-question fields (textareas + free-text inputs)
    with their labels.

    Skips cover-letter fields and anything that looks like a known contact /
    education field — those are handled by the dedicated fillers. Each result
    carries `tag` ('textarea' | 'input') so _apply_screening_answers can build
    the right selector."""
    try:
        return page.evaluate(
            r"""
            () => {
              const out = [];
              // Substrings that mark a field as a known contact/education
              // field — we never want the LLM to "answer" these.
              const CONTACT_HINTS = [
                'first name','last name','full name','preferred name','middle name',
                'email','phone','mobile','location','address','city','state',
                'zip','postal','country','school','university','college',
                'degree','discipline','major','field of study','start date',
                'end date','start year','end year','start month','end month',
                'graduation','linkedin','github','website','portfolio','resume',
                'résumé','cover letter','how did you hear'
              ];
              const getLabel = (el) => {
                if (el.id) {
                  const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                  if (l) return l.innerText.trim();
                }
                const aria = el.getAttribute('aria-label');
                if (aria) return aria.trim();
                const lb = el.getAttribute('aria-labelledby');
                if (lb) {
                  const ref = document.getElementById(lb);
                  if (ref) return ref.innerText.trim();
                }
                const pl = el.closest('label');
                if (pl) return pl.innerText.trim();
                const fs = el.closest('fieldset, .field, .form-group, [class*="question" i], [class*="Field" i]');
                if (fs) {
                  const q = fs.querySelector('legend, label, [class*="question" i], [class*="Label" i]');
                  if (q) return q.innerText.trim();
                  return fs.innerText.split('\n')[0].trim();
                }
                return (el.placeholder || el.name || '').trim();
              };
              const consider = (el, tag) => {
                if (el.disabled || el.readOnly) return;
                if (el.value && el.value.trim()) return;
                if (!el.offsetParent) return;
                const label = getLabel(el);
                if (!label || label.length < 8) return;
                const lower = (label + ' ' + (el.name||'') + ' ' + (el.id||'') + ' ' +
                               (el.placeholder||'') + ' ' + (el.getAttribute('aria-label')||'')).toLowerCase();
                if (lower.includes('cover') || lower.includes('letter')) return;
                if (CONTACT_HINTS.some(h => lower.includes(h))) return;
                // Inputs that are part of a combobox/typeahead — skip, .fill()
                // won't register the choice.
                if (tag === 'input' && (el.getAttribute('role') === 'combobox' ||
                    el.getAttribute('aria-autocomplete'))) return;
                out.push({tag, label: label.slice(0, 240),
                          name: el.name || '', id: el.id || '', idx: out.length});
              };
              document.querySelectorAll('textarea').forEach(t => consider(t, 'textarea'));
              document.querySelectorAll(
                'input[type="text"], input:not([type])'
              ).forEach(i => consider(i, 'input'));
              return out;
            }
            """
        ) or []
    except Exception as e:
        log.debug("Screening Q detect skipped: %s", e)
        return []


def _llm_answer_questions_sync(questions: list[dict], job: dict) -> dict[str, str]:
    """Synchronously ask Claude (haiku) to answer each screening question.

    Runs `claude -p ...` as a subprocess so this works from inside the sync
    fill_application path. Resume is read from the vault; answers are
    constrained to honest, resume-grounded responses."""
    if not questions:
        return {}
    try:
        resume = VAULT_RESUME.read_text(encoding="utf-8") if VAULT_RESUME.exists() else ""
    except Exception:
        resume = ""

    qlist = "\n".join(
        f"{i+1}. {q['label']}" for i, q in enumerate(questions)
    )
    tailored_hook = (job.get("tailored") or {}).get("hook", "")
    prompt = f"""You are answering job-application screening questions for Taran Singh.

RESUME (source of truth — do not invent anything beyond this):
{resume[:3000]}

JOB: {job.get('company','')} — {job.get('role','')}
JD REQUIREMENTS: {job.get('jd_summary','(not specified)')}
COVER HOOK (already written): {tailored_hook}

Answer each question concisely (under ~120 words). Be honest about experience
levels — Taran is a Penn State AI Engineering student graduating May 2028, with
internship + project experience (not full-time). For "years of experience with X"
questions, give a small honest number (1 if from coursework/projects, 2 if from
internship work). For "why this company" / "why this role", reference one
specific resume fact and one specific JD requirement. No filler.

QUESTIONS:
{qlist}

Return ONLY a JSON array of strings — one answer per question, in order:
["answer 1", "answer 2", ...]
"""

    # The Claude CLI can return a short usage-limit / overload message instead
    # of an answer. Retry once after a short wait, mirroring tools.llm's own
    # usage-limit handling — otherwise a transient blip silently drops every
    # screening answer for the application.
    _TRANSIENT = ("usage limit", "limit reached", "out of usage",
                  "out of extra usage", "overloaded", "529", "try again",
                  "please try")

    def _call_claude(p: str) -> str:
        res = subprocess.run(
            [
                "claude", "-p", p,
                "--model", "haiku",
                "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                "--allowedTools", "",
                "--dangerously-skip-permissions",
            ],
            capture_output=True, text=True, timeout=120,
        )
        return res.stdout or ""

    out = ""
    for attempt in (1, 2):
        try:
            out = _call_claude(prompt)
        except subprocess.TimeoutExpired:
            log.warning("LLM screening Q timed out (attempt %d/2)", attempt)
            out = ""
        except Exception as e:
            log.warning("LLM screening Q failed (attempt %d/2): %s", attempt, e)
            out = ""

        m = re.search(r"\[.*\]", out, re.S)
        if m:
            try:
                arr = _json.loads(m.group(0))
            except Exception:
                arr = None
            if isinstance(arr, list):
                answers = {}
                for q, a in zip(questions, arr):
                    if isinstance(a, str) and a.strip():
                        answers[q["label"]] = a.strip()
                log.info("LLM answered %d/%d screening questions",
                         len(answers), len(questions))
                return answers

        low = out.lower()
        transient = (not out.strip()) or any(k in low for k in _TRANSIENT)
        if attempt == 1 and transient:
            log.warning("LLM screening: transient response (%d chars) — retry in 25s",
                        len(out))
            time.sleep(25)
            continue
        log.warning("LLM screening: no usable answer after attempt %d (%d chars)",
                    attempt, len(out))
        return {}
    return {}


def _apply_screening_answers(page, questions: list[dict],
                             answers: dict[str, str], fields_filled: list):
    """Type each LLM-generated answer into its matching field (textarea or
    free-text input), located by id then name."""
    for q in questions:
        ans = answers.get(q["label"])
        if not ans:
            continue
        tag = q.get("tag", "textarea")
        selectors = []
        if q.get("id"):
            # CSS.escape isn't available here — use an attribute selector,
            # which tolerates ids with digits/colons (greenhouse question_NNN).
            selectors.append(f'{tag}[id="{q["id"]}"]')
        if q.get("name"):
            selectors.append(f'{tag}[name="{q["name"]}"]')
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=400):
                    el.fill(ans)
                    fields_filled.append({
                        "field": f"screening:{q['label'][:70]}",
                        "value": ans[:90],
                    })
                    break
            except Exception:
                continue


def _fill_extras(page, job: dict, fields_filled: list):
    """Run all the post-contact-field fillers in one go: cover letter, EEO,
    LLM-generated screening answers. Safe to call on every platform."""
    tailored = job.get("tailored") or {}
    hook = tailored.get("hook", "") or ""
    _fill_cover_letter(page, hook, fields_filled)
    _fill_eeo_decline(page, fields_filled)
    questions = _detect_screening_questions(page)
    if questions:
        log.info("Detected %d screening question(s) — asking LLM", len(questions))
        answers = _llm_answer_questions_sync(questions, job)
        if answers:
            _apply_screening_answers(page, questions, answers, fields_filled)


# ── Platform-specific fillers ─────────────────────────────────────────────────

def _fill_greenhouse(page, fields_filled: list, job: dict | None = None,
                     resume_path: str | None = None):
    """Fill a Greenhouse.io application form using known IDs.

    Covers both the classic boards.greenhouse.io layout and the modern
    job-boards.greenhouse.io layout (id-based, with #candidate-location)."""
    # Basic contact fields — id-based, present on both layouts.
    for field_id, key, value in [
        ("#first_name",        "first_name",     CONTACT["first_name"]),
        ("#last_name",         "last_name",      CONTACT["last_name"]),
        ("#preferred_name",    "preferred_name", CONTACT["first_name"]),
        ("#email",             "email",          CONTACT["email"]),
        ("#phone",             "phone",          CONTACT["phone"]),
        ("#candidate-location","location",       CONTACT["location"]),
        ("#country",           "country",        CONTACT["country"]),
    ]:
        try:
            el = page.locator(field_id).first
            if el.is_visible(timeout=600):
                el.fill(value)
                # Location / country are typeahead combos — commit the choice.
                if field_id in ("#candidate-location", "#country"):
                    page.wait_for_timeout(700)
                    try:
                        page.keyboard.press("ArrowDown")
                        page.keyboard.press("Enter")
                    except Exception:
                        pass
                fields_filled.append({"field": key, "value": value})
        except Exception:
            pass

    # LinkedIn / GitHub
    _try_fill(page, ['input[name*="linkedin" i]', 'input[id*="linkedin" i]',
                     'input[placeholder*="LinkedIn" i]', 'input[aria-label*="LinkedIn" i]'],
              CONTACT["linkedin"], "linkedin", fields_filled)
    _try_fill(page, ['input[name*="github" i]', 'input[id*="github" i]',
                     'input[placeholder*="GitHub" i]', 'input[aria-label*="GitHub" i]'],
              CONTACT["github"], "github", fields_filled)

    # Education — Greenhouse uses --0 suffix for first entry
    for field_id, key, value in [
        ("#school--0",     "university", CONTACT["university"]),
        ("#discipline--0", "major",      CONTACT["major"]),
        ("#start-year--0", "start_year", CONTACT["start_year"]),
        ("#end-year--0",   "grad_year",  CONTACT["grad_year"]),
    ]:
        try:
            el = page.locator(field_id).first
            if el.is_visible(timeout=600):
                el.fill(value)
                fields_filled.append({"field": key, "value": value})
        except Exception:
            pass

    # Degree dropdown
    for sel in ["#degree--0", 'select[id*="degree" i]', 'select[name*="degree" i]']:
        if _try_select(page, sel, "Bachelor's Degree", "degree", fields_filled):
            break

    # Country dropdown
    for sel in ['select[name="job_application[country]"]', "#country", 'select[id*="country" i]']:
        if _try_select(page, sel, "United States", "country", fields_filled):
            break

    # Work authorization — common in finance/trading roles
    for label_text in ["Yes", "I am authorized to work", "U.S. Citizen"]:
        try:
            cb = page.locator(
                f'input[type="radio"] ~ label:has-text("{label_text}"), '
                f'label:has-text("{label_text}") input[type="radio"]'
            ).first
            if cb.is_visible(timeout=300):
                cb.click()
                fields_filled.append({"field": "work_auth", "value": label_text})
                break
        except Exception:
            pass

    _upload_resume(page, fields_filled, resume_path)
    if job is not None:
        _fill_extras(page, job, fields_filled)


def _fill_lever(page, fields_filled: list, job: dict | None = None,
                resume_path: str | None = None):
    """Fill a Lever application form."""
    _try_fill(page, ['input[name="name"]', 'input[placeholder*="Full name" i]'],
              CONTACT["full_name"], "full_name", fields_filled)
    _try_fill(page, ['input[name="email"]', 'input[type="email"]'],
              CONTACT["email"], "email", fields_filled)
    _try_fill(page, ['input[name="phone"]', 'input[type="tel"]'],
              CONTACT["phone"], "phone", fields_filled)
    _try_fill(page, ['input[name*="linkedin" i]', 'input[placeholder*="LinkedIn" i]'],
              CONTACT["linkedin"], "linkedin", fields_filled)
    _try_fill(page, ['input[name*="github" i]', 'input[placeholder*="GitHub" i]'],
              CONTACT["github"], "github", fields_filled)
    _upload_resume(page, fields_filled, resume_path)
    if job is not None:
        _fill_extras(page, job, fields_filled)


def _fill_ashby(page, fields_filled: list, job: dict | None = None,
                resume_path: str | None = None):
    """Fill an Ashby application form.

    Ashby uses stable _systemfield_* IDs for the canonical fields and renders
    custom questions inside [data-qid] blocks with a labeled <textarea> /
    <input>. We hit the system fields first, then let the generic + extras
    pass handle anything custom."""
    for field_id, key, value in [
        ("_systemfield_name",     "full_name", CONTACT["full_name"]),
        ("_systemfield_email",    "email",     CONTACT["email"]),
        ("_systemfield_phone",    "phone",     CONTACT["phone"]),
        ("_systemfield_location", "location",  CONTACT["location"]),
    ]:
        sel = f'input[id="{field_id}"], input[name="{field_id}"]'
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=500):
                el.fill(value)
                fields_filled.append({"field": key, "value": value})
        except Exception:
            pass

    # Resume / LinkedIn / GitHub
    _try_fill(page, ['input[id*="resume" i][type="url"]',
                     'input[name*="resume" i][type="url"]'],
              "", "resume_url", fields_filled)
    _try_fill(page, ['input[id*="linkedin" i]', 'input[name*="linkedin" i]',
                     'input[aria-label*="LinkedIn" i]'],
              CONTACT["linkedin"], "linkedin", fields_filled)
    _try_fill(page, ['input[id*="github" i]', 'input[name*="github" i]',
                     'input[aria-label*="GitHub" i]'],
              CONTACT["github"], "github", fields_filled)

    _upload_resume(page, fields_filled, resume_path)
    if job is not None:
        _fill_extras(page, job, fields_filled)


def _fill_smartrecruiters(page, fields_filled: list, job: dict | None = None,
                          resume_path: str | None = None):
    """Fill a SmartRecruiters application form.

    SmartRecruiters fields use camelCase name attributes (firstName, lastName,
    phoneNumber, currentLocation, etc.) and a stable structure for the
    education + experience repeaters."""
    for sel, key, value in [
        ('input[name="firstName"]',        "first_name", CONTACT["first_name"]),
        ('input[name="lastName"]',         "last_name",  CONTACT["last_name"]),
        ('input[name="email"]',            "email",      CONTACT["email"]),
        ('input[name="phoneNumber"]',      "phone",      CONTACT["phone"]),
        ('input[name="phone"]',            "phone",      CONTACT["phone"]),
        ('input[name="currentLocation"]',  "location",   CONTACT["location"]),
        ('input[name="zipCode"]',          "zip",        CONTACT["zip"]),
        ('input[name="city"]',             "city",       CONTACT["city"]),
        ('input[name*="linkedin" i]',      "linkedin",   CONTACT["linkedin"]),
        ('input[name*="github" i]',        "github",     CONTACT["github"]),
    ]:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=500):
                el.fill(value)
                fields_filled.append({"field": key, "value": value})
        except Exception:
            pass

    # Education repeater (first entry)
    _try_fill(page,
              ['input[name*="institution" i]', 'input[name*="school" i]'],
              CONTACT["university"], "university", fields_filled)
    _try_fill(page,
              ['input[name*="major" i]', 'input[name*="field" i]'],
              CONTACT["major"], "major", fields_filled)

    _upload_resume(page, fields_filled, resume_path)
    if job is not None:
        _fill_extras(page, job, fields_filled)


def _service_from_url(url: str) -> str:
    """Extract a stable service key (registrable domain) from a URL for credential lookup."""
    from urllib.parse import urlparse
    host = urlparse(url).hostname or ""
    parts = host.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def _auto_login(page, service_hint: str, fields_filled: list) -> bool:
    """
    If a login form is present, fetch creds for service_hint from the PAIS keychain,
    fill, and submit. If no credentials exist, generate a new password, fill it,
    submit a Create-Account flow, and store the new credentials.

    Returns True if we attempted (and likely completed) a login or signup, else False.
    """
    try:
        from tools import credentials
    except Exception as e:
        log.warning("credentials module unavailable: %s", e)
        return False

    email_selectors = [
        'input[type="email"]', 'input[autocomplete="email"]',
        'input[autocomplete="username"]', 'input[name="email"]',
        'input[id*="email" i]', 'input[name*="username" i]', 'input[id*="username" i]',
    ]
    password_selectors = [
        'input[type="password"]', 'input[autocomplete="current-password"]',
        'input[autocomplete="new-password"]', 'input[name*="password" i]',
        'input[id*="password" i]',
    ]

    pw_field = None
    for sel in password_selectors:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=500):
                pw_field = el
                break
        except Exception:
            continue
    if pw_field is None:
        return False

    email_field = None
    for sel in email_selectors:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=400):
                email_field = el
                break
        except Exception:
            continue

    existing = credentials.get(service_hint)
    is_signup = _detect_signup_form(page)

    if existing and not is_signup:
        if email_field:
            try:
                email_field.fill(existing["account"] or CONTACT["email"])
            except Exception:
                pass
        try:
            pw_field.fill(existing["password"])
            fields_filled.append({"field": "login", "value": "<from keychain>"})
        except Exception as e:
            log.warning("Failed to fill stored password: %s", e)
            return False
        _submit_auth_form(page, signup=False)
        return True

    # Sign-up path: generate + store a new password.
    new_pw = credentials.generate_password()
    if email_field:
        try:
            email_field.fill(CONTACT["email"])
        except Exception:
            pass
    try:
        pw_field.fill(new_pw)
    except Exception as e:
        log.warning("Failed to fill new password: %s", e)
        return False
    # Some forms have a confirm-password field — fill the same value.
    try:
        confirm = page.locator(
            'input[type="password"][name*="confirm" i], input[type="password"][id*="confirm" i], '
            'input[type="password"][autocomplete="new-password"]:nth-of-type(2)'
        ).first
        if confirm.is_visible(timeout=400):
            confirm.fill(new_pw)
    except Exception:
        pass
    credentials.store(service_hint, CONTACT["email"], new_pw, notes="auto-created by PAIS")
    fields_filled.append({"field": "signup", "value": "<stored to keychain>"})
    _submit_auth_form(page, signup=True)
    return True


def _detect_signup_form(page) -> bool:
    for sel in [
        'button:has-text("Create account")', 'button:has-text("Sign up")',
        'button:has-text("Register")', 'a:has-text("Create account")',
        'input[type="password"][autocomplete="new-password"]',
    ]:
        try:
            if page.locator(sel).first.is_visible(timeout=300):
                return True
        except Exception:
            continue
    return False


def _submit_auth_form(page, signup: bool):
    candidates = (
        ['button:has-text("Create Account")', 'button:has-text("Sign up")',
         'button:has-text("Register")', 'button[type="submit"]']
        if signup else
        ['button:has-text("Sign in")', 'button:has-text("Log in")',
         'button:has-text("Login")', 'button[type="submit"]']
    )
    for sel in candidates:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=400):
                el.click()
                page.wait_for_timeout(2500)
                return
        except Exception:
            continue


def _fill_workday(page, fields_filled: list, url: str,
                  job: dict | None = None, resume_path: str | None = None) -> dict:
    """Attempt auto-login on Workday before bailing to manual mode."""
    page.wait_for_timeout(2000)
    _click_apply(page)
    page.wait_for_timeout(1500)

    service = _service_from_url(url)
    logged_in = _auto_login(page, service, fields_filled)
    if logged_in:
        page.wait_for_timeout(3000)
        _fill_generic(page, fields_filled, url, job=job, resume_path=resume_path)
        screenshot_bytes = page.screenshot()
        return {
            "platform": "workday",
            "fields_filled": fields_filled,
            "screenshot_b64":   base64.b64encode(screenshot_bytes).decode(),
            "screenshot_bytes": screenshot_bytes,
            "message": "Logged in via stored credentials; form fields auto-filled.",
        }

    screenshot_bytes = page.screenshot()
    return {
        "needs_manual_login": True,
        "platform": "workday",
        "fields_filled": [],
        "screenshot_b64":   base64.b64encode(screenshot_bytes).decode(),
        "screenshot_bytes": screenshot_bytes,
        "message": (
            "Workday login wall and no stored credentials for "
            f"{service}. Sign in once, then run /cred_add to remember the password."
        ),
    }


def _fill_generic(page, fields_filled: list, url: str = "",
                  job: dict | None = None, resume_path: str | None = None):
    """CSS-selector-based filler for unknown ATSs."""
    _click_apply(page)
    page.wait_for_timeout(500)
    if url:
        _auto_login(page, _service_from_url(url), fields_filled)
        page.wait_for_timeout(800)
    for field_key, selectors in FIELD_MAP.items():
        value = CONTACT.get(field_key, "")
        if not value:
            continue
        _try_fill(page, selectors, value, field_key, fields_filled)
    _upload_resume(page, fields_filled, resume_path)
    if job is not None:
        _fill_extras(page, job, fields_filled)


# ── Public entry point ────────────────────────────────────────────────────────

def fill_application(job: dict, keep_open: bool = False) -> dict:
    """
    Open the job URL in the persistent (logged-in) Chromium profile, fill what
    we can, screenshot, and return a result dict.

    keep_open=False — batch search: the browser closes after the screenshot.
    keep_open=True  — manual "Open & Fill": the browser stays open so Taran
                      reviews and clicks Submit himself.
    A Workday page that needs manual login is always left open regardless.

    Only one review browser is ever open at a time (the persistent profile
    can't be driven by two contexts at once, and this stops windows piling up).
    """
    global _active

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "error": "Playwright not installed. Run: pip install playwright && playwright install chromium",
            "fields_filled": [], "screenshot_b64": "", "platform": "unknown",
        }

    url = job.get("url", "")
    platform = _detect_platform(url)
    fields_filled: list[dict] = []
    screenshot_b64 = ""
    screenshot_bytes = b""

    # Prefer the per-job tailored resume PDF; _upload_resume falls back to the
    # generic resume.pdf when this is None.
    job_id = re.sub(r"[^A-Za-z0-9_-]", "", str(job.get("id", "job"))) or "job"
    tailored_pdf = Path(__file__).parent.parent / "career_resumes" / f"{job_id}.pdf"
    resume_path = str(tailored_pdf) if tailored_pdf.exists() else None

    pw = None
    context = None
    keep = keep_open
    try:
        # Free the persistent profile + any prior review window first — the
        # profile can only be driven by one context at a time.
        _close_active()

        # Start a fresh Playwright in THIS thread. (sync_playwright instances
        # are bound to their creating thread via greenlets — caching across
        # asyncio.to_thread worker threads throws greenlet.error.)
        pw = sync_playwright().start()

        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            slow_mo=300,
            viewport={"width": 1280, "height": 900},
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"),
            args=["--start-maximized", "--disable-blink-features=AutomationControlled",
                  "--no-first-run", "--no-default-browser-check"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)

        # themuse.com landing pages aren't apply forms — they're descriptions
        # with an "Apply on company site" button that opens the employer's
        # real ATS (greenhouse / lever / workday / company site). Resolve it,
        # navigate the main page there, then re-detect the platform so the
        # right filler runs.
        if "themuse.com" in url:
            new_url = _follow_themuse_apply(page)
            if new_url and "themuse.com" not in new_url:
                log.info("themuse → %s", new_url)
                url = new_url
                platform = _detect_platform(url)
                page.goto(url, timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(2500)
            else:
                log.warning("themuse apply URL not resolved for %s", url)

        # ATS pages (esp. React Greenhouse job-boards) fire domcontentloaded
        # long before the form inputs mount — wait for a real field first so
        # the contact-field loop doesn't run against an empty DOM.
        _wait_for_form(page)

        if platform == "greenhouse":
            # The greenhouse form is inline on both boards.greenhouse.io and
            # job-boards.greenhouse.io — no apply click needed. (On job-boards
            # the "Apply" button IS the submit button; clicking it here would
            # fire a premature submit.)
            _fill_greenhouse(page, fields_filled, job=job, resume_path=resume_path)

        elif platform == "workday":
            result = _fill_workday(page, fields_filled, url, job=job, resume_path=resume_path)
            ss_dir = Path(__file__).parent.parent / "screenshots"
            ss_dir.mkdir(exist_ok=True)
            (ss_dir / f"{job.get('id', 'job')}.png").write_bytes(result.get("screenshot_bytes", b""))
            # Workday: keep the window open if the caller asked OR a manual login is needed.
            keep = keep or bool(result.get("needs_manual_login"))
            return result

        elif platform == "lever":
            _click_apply(page)
            page.wait_for_timeout(1500)
            _fill_lever(page, fields_filled, job=job, resume_path=resume_path)

        elif platform == "ashby":
            _click_apply(page)
            page.wait_for_timeout(1500)
            _fill_ashby(page, fields_filled, job=job, resume_path=resume_path)

        elif platform == "smartrecruiters":
            _click_apply(page)
            page.wait_for_timeout(1500)
            _fill_smartrecruiters(page, fields_filled, job=job, resume_path=resume_path)

        else:
            _fill_generic(page, fields_filled, url, job=job, resume_path=resume_path)

        # Screenshot — full page so the review screenshot shows the filled
        # form, not just the job-description header. Scroll the first form
        # field into view first so lazy-rendered inputs are painted.
        try:
            page.evaluate("""
                () => {
                  const f = document.querySelector(
                    'input#first_name, input[name="first_name"], input[name="firstName"], '
                    + 'input[type="email"], input[name="name"]');
                  if (f) f.scrollIntoView({block: 'center'});
                }
            """)
        except Exception:
            pass
        page.wait_for_timeout(800)
        try:
            screenshot_bytes = page.screenshot(full_page=True)
        except Exception:
            screenshot_bytes = page.screenshot()
        screenshot_b64   = base64.b64encode(screenshot_bytes).decode()

        # Persist screenshot + details JSON
        ss_dir  = Path(__file__).parent.parent / "screenshots"
        ss_dir.mkdir(exist_ok=True)
        jid     = job.get("id", "job")
        ss_path = ss_dir / f"{jid}.png"
        ss_path.write_bytes(screenshot_bytes)
        (ss_dir / f"{jid}_details.json").write_text(_json.dumps({
            "company":         job.get("company"),
            "role":            job.get("role"),
            "url":             url,
            "platform":        platform,
            "fields_filled":   fields_filled,
            "tailored_bullets": job.get("tailored", {}).get("bullets", []),
            "cover_hook":      job.get("tailored", {}).get("hook", ""),
            "resume_uploaded": Path(resume_path).name if resume_path else "resume.pdf",
            "screenshot_path": str(ss_path),
        }, ensure_ascii=False, indent=2))

        log.info("[%s] Filled %d fields for %s (keep_open=%s)",
                 platform, len(fields_filled), job.get("company"), keep)

    except Exception as e:
        log.exception("fill_application error for %s (%s)", url, platform)
        return {
            "error": str(e), "fields_filled": fields_filled,
            "screenshot_b64": screenshot_b64, "platform": platform,
        }
    finally:
        if context is not None:
            if keep:
                _active = {"pw": pw, "ctx": context}   # leave open for review
            else:
                try:
                    context.close()
                except Exception:
                    pass
                try:
                    if pw is not None:
                        pw.stop()
                except Exception:
                    pass
        elif pw is not None:
            # Context never opened — still stop the playwright instance.
            try:
                pw.stop()
            except Exception:
                pass

    return {
        "fields_filled":   fields_filled,
        "screenshot_b64":  screenshot_b64,
        "screenshot_bytes": screenshot_bytes,
        "platform":        platform,
    }


def _follow_themuse_apply(page) -> str:
    """On a themuse.com landing page, resolve the employer's real ATS URL.

    themuse renders the apply control as a "Apply on company site" <button>
    that opens the employer's Greenhouse / Lever / Workday page in a NEW TAB.
    We click the visible one, capture the popup's URL, close the popup, and
    return the URL — the caller then navigates the main page there.

    Returns "" if no apply control is found or it never left themuse."""
    try:
        ctx = page.context
        btns = page.locator('button:has-text("Apply on company site")')
        target = None
        for i in range(btns.count()):
            el = btns.nth(i)
            try:
                if el.is_visible():
                    target = el
                    break
            except Exception:
                continue

        if target is not None:
            try:
                with ctx.expect_page(timeout=15000) as pop_info:
                    target.click(force=True)
                popup = pop_info.value
                popup.wait_for_load_state("domcontentloaded", timeout=20000)
                popup.wait_for_timeout(2000)
                dest = popup.url
                try:
                    popup.close()
                except Exception:
                    pass
                return dest if "themuse.com" not in dest else ""
            except Exception as e:
                log.debug("themuse apply popup not captured: %s", e)

        # Fallback — a plain apply anchor pointing off themuse.
        href = page.evaluate(
            r"""
            () => {
              const a = Array.from(document.querySelectorAll('a'))
                .find(a => /apply/i.test(a.innerText || '') && a.href
                           && !/themuse\.com/.test(a.href));
              return a ? a.href : '';
            }
            """
        ) or ""
        return href
    except Exception as e:
        log.debug("themuse apply follow skipped: %s", e)
        return ""


# ── Bootstrap-login (manual sign-in helper) ───────────────────────────────────

def open_for_bootstrap(url: str) -> dict:
    """Open the persistent profile to `url` and leave the browser open so Taran
    can sign in manually. Once he completes the login, cookies persist in the
    shared profile and subsequent auto-fills against that tenant succeed.

    Used by the Workday "Bootstrap Login" button on the career detail pane."""
    global _active

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"ok": False, "error": "Playwright not installed"}

    pw = None
    try:
        _close_active()
        pw = sync_playwright().start()
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            slow_mo=200,
            viewport={"width": 1280, "height": 900},
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"),
            args=["--start-maximized", "--disable-blink-features=AutomationControlled",
                  "--no-first-run", "--no-default-browser-check"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        _active = {"pw": pw, "ctx": context}  # leave open for manual sign-in
        log.info("Bootstrap-login window opened for %s", _service_from_url(url))
        return {"ok": True, "service": _service_from_url(url)}
    except Exception as e:
        log.exception("open_for_bootstrap failed for %s", url)
        try:
            if pw is not None:
                pw.stop()
        except Exception:
            pass
        return {"ok": False, "error": str(e)}
