"""
Targeted Greenhouse.io application filler for Aquatic Capital Management.
Greenhouse forms use specific IDs: #first_name, #last_name, #email, #phone, etc.
"""
import json
import base64
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

CONTACT = {
    "first_name": "Taranveer",
    "last_name": "Singh",
    "email": "***REDACTED***",
    "phone": "***REDACTED***",
    "linkedin": "https://linkedin.com/in/taranveer-singh",
    "github": "https://github.com/Taran132g",
    "website": "https://taran.ai",
    "school": "Pennsylvania State University",
    "major": "Artificial Intelligence Engineering",
    "grad_year": "2027",
}

GREENHOUSE_FIELDS = {
    "first_name":   ["#first_name", 'input[name="job_application[first_name]"]'],
    "last_name":    ["#last_name",  'input[name="job_application[last_name]"]'],
    "email":        ["#email",      'input[name="job_application[email]"]'],
    "phone":        ["#phone",      'input[name="job_application[phone_number]"]'],
    "linkedin":     ['input[name="job_application[answers_attributes][0][text_value]"][id*="linkedin" i]',
                     '#job_application_answers_attributes_0_text_value',
                     'input[id*="linkedin" i]',
                     'input[placeholder*="LinkedIn" i]'],
    "website":      ['input[id*="website" i]', 'input[placeholder*="website" i]',
                     'input[name*="website" i]'],
    "github":       ['input[id*="github" i]', 'input[placeholder*="GitHub" i]'],
    "school":       ['input[id*="school" i]', 'input[placeholder*="school" i]',
                     'select[id*="school" i]'],
    "major":        ['input[id*="major" i]', 'input[placeholder*="major" i]',
                     'select[id*="discipline" i]'],
}

URL = "https://job-boards.greenhouse.io/aquaticcapitalmanagement/jobs/8489233002"
JOB_ID = "job_1"
CACHE_PATH = Path(__file__).parent / "career_jobs_cache.json"
SS_DIR = Path(__file__).parent / "screenshots"

def fill_greenhouse():
    from playwright.sync_api import sync_playwright

    SS_DIR.mkdir(exist_ok=True)
    fields_filled = []
    screenshots = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=400, args=["--start-maximized"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 900},
                                  user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")
        page = ctx.new_page()

        log.info("Navigating to listing page...")
        page.goto(URL, timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        # Screenshot of listing page
        ss1 = page.screenshot()
        (SS_DIR / f"{JOB_ID}_listing.png").write_bytes(ss1)

        # Click Apply button
        log.info("Clicking Apply...")
        try:
            apply_btn = page.locator('a:has-text("Apply"), button:has-text("Apply"), a:has-text("Apply Now"), button:has-text("Apply Now")').first
            if apply_btn.is_visible(timeout=3000):
                apply_btn.click()
                page.wait_for_timeout(3000)
                log.info("Clicked Apply, waiting for form...")
        except Exception as e:
            log.warning("Apply button not found: %s", e)

        # Check what URL we're on now
        log.info("Current URL: %s", page.url)

        # Screenshot after clicking Apply
        ss2 = page.screenshot()
        (SS_DIR / f"{JOB_ID}_after_apply_click.png").write_bytes(ss2)

        # Greenhouse application form — try to fill fields
        for field_key, selectors in GREENHOUSE_FIELDS.items():
            value = CONTACT.get(field_key, "")
            if not value:
                continue
            filled = False
            for selector in selectors:
                try:
                    el = page.locator(selector).first
                    if el.is_visible(timeout=800):
                        tag = el.evaluate("el => el.tagName.toLowerCase()")
                        if tag == "select":
                            # Try to select by label text
                            try:
                                el.select_option(label=value)
                                fields_filled.append({"field": field_key, "value": value})
                                log.info("Selected %s: %s", field_key, value)
                            except Exception:
                                el.select_option(index=1)
                                fields_filled.append({"field": field_key, "value": "(selected option 1)"})
                        else:
                            el.triple_click()
                            el.fill(value)
                            fields_filled.append({"field": field_key, "value": value})
                            log.info("Filled %s: %s", field_key, value)
                        filled = True
                        break
                except Exception:
                    continue
            if not filled:
                log.info("Could not find field: %s", field_key)

        # Scroll and screenshot the filled form
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(800)
        ss_top = page.screenshot()
        (SS_DIR / f"{JOB_ID}_filled_top.png").write_bytes(ss_top)
        page.evaluate("window.scrollTo(0, 500)")
        page.wait_for_timeout(500)
        ss_mid = page.screenshot()
        (SS_DIR / f"{JOB_ID}_filled_mid.png").write_bytes(ss_mid)

        # Main screenshot for job_1.png
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(400)
        ss_main = page.screenshot()
        (SS_DIR / f"{JOB_ID}.png").write_bytes(ss_main)

        # Get all visible input fields to understand what's on the page
        all_inputs = page.evaluate("""
            () => Array.from(document.querySelectorAll('input, textarea, select')).map(el => ({
                tag: el.tagName,
                type: el.type || '',
                id: el.id || '',
                name: el.name || '',
                placeholder: el.placeholder || '',
                label: el.labels && el.labels[0] ? el.labels[0].textContent.trim() : ''
            }))
        """)
        log.info("All form fields on page:")
        for inp in all_inputs[:30]:
            log.info("  %s", inp)

        log.info("Browser left open. Filled %d fields.", len(fields_filled))
        log.info("Fields: %s", [f['field'] for f in fields_filled])

        # Keep browser open
        input("Press Enter to close browser and continue...")

    return {
        "fields_filled": fields_filled,
        "screenshot_b64": base64.b64encode(ss_main).decode(),
        "screenshot_bytes": ss_main,
    }


if __name__ == "__main__":
    result = fill_greenhouse()
    print(f"\nFilled {len(result['fields_filled'])} fields:")
    for f in result['fields_filled']:
        print(f"  {f['field']}: {f['value']}")

    # Update cache
    jobs = json.loads(CACHE_PATH.read_text())
    for job in jobs:
        if job["id"] == JOB_ID:
            job["fill_result"] = {"fields_filled": result["fields_filled"], "error": ""}
            job["status"] = "needs_review"
            job["screenshot_b64"] = result["screenshot_b64"]
    CACHE_PATH.write_text(json.dumps(jobs, ensure_ascii=False, indent=2))
    print("Cache updated.")
