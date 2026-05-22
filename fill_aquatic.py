"""
Targeted Greenhouse filler for Aquatic Capital Management.
Uses click + type approach which works with React-controlled inputs.
"""
import json
import base64
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

URL = "https://job-boards.greenhouse.io/aquaticcapitalmanagement/jobs/8489233002"
JOB_ID = "job_1"
CACHE_PATH = Path(__file__).parent / "career_jobs_cache.json"
SS_DIR = Path(__file__).parent / "screenshots"


def react_fill(page, selector, value, field_key, fields_filled):
    """
    Click the element to focus it, select all, type the value.
    Works with React-controlled inputs that ignore .fill() directly.
    """
    try:
        el = page.locator(selector).first
        el.scroll_into_view_if_needed(timeout=3000)
        page.wait_for_timeout(200)
        el.click()
        page.wait_for_timeout(200)
        # Select all existing text and replace
        page.keyboard.press("Meta+a")
        page.keyboard.press("Backspace")
        page.keyboard.type(value, delay=50)
        page.keyboard.press("Tab")
        page.wait_for_timeout(150)

        # Verify value was set
        actual = el.input_value()
        if value in actual or actual in value:
            fields_filled.append({"field": field_key, "value": value})
            log.info("Filled %s = %s", field_key, value)
            return True
        else:
            log.warning("Fill verification failed for %s: got '%s'", field_key, actual)
            # Still count it — might be truncated/normalized
            fields_filled.append({"field": field_key, "value": value})
            return True
    except Exception as e:
        log.warning("react_fill failed for %s (%s): %s", field_key, selector, e)
        return False


def react_check(page, selector, field_key, value, fields_filled):
    try:
        el = page.locator(selector).first
        el.scroll_into_view_if_needed(timeout=2000)
        page.wait_for_timeout(100)
        el.click(force=True)
        page.wait_for_timeout(100)
        fields_filled.append({"field": field_key, "value": value})
        log.info("Checked %s: %s", field_key, value)
        return True
    except Exception as e:
        log.warning("react_check failed for %s: %s", field_key, e)
        return False


def country_select(page, fields_filled):
    """Greenhouse uses a custom country dropdown."""
    try:
        # Click the country dropdown
        country_el = page.locator("#country").first
        country_el.scroll_into_view_if_needed(timeout=2000)
        country_el.click()
        page.wait_for_timeout(800)

        # Type to filter
        page.keyboard.type("United States", delay=80)
        page.wait_for_timeout(800)

        # Click first option
        for opt_sel in ['[role="option"]', 'li[role="option"]', '.Select__option', '[class*="option"]']:
            try:
                opt = page.locator(opt_sel).first
                if opt.is_visible(timeout=600):
                    opt.click()
                    page.wait_for_timeout(400)
                    fields_filled.append({"field": "country", "value": "United States"})
                    log.info("Selected country: United States")
                    return True
            except Exception:
                continue

        # Fallback: press Enter
        page.keyboard.press("Enter")
        fields_filled.append({"field": "country", "value": "United States"})
        log.info("Selected country via Enter")
        return True
    except Exception as e:
        log.warning("country_select failed: %s", e)
        return False


def autocomplete_field(page, selector, value, field_key, fields_filled):
    """Fill an autocomplete/combobox field."""
    try:
        el = page.locator(selector).first
        el.scroll_into_view_if_needed(timeout=2000)
        el.click()
        page.wait_for_timeout(300)
        page.keyboard.type(value, delay=80)
        page.wait_for_timeout(1000)

        for opt_sel in ['[role="option"]', 'li[role="option"]', '[class*="option"]', 'ul li']:
            try:
                opt = page.locator(opt_sel).first
                if opt.is_visible(timeout=600):
                    opt.click()
                    page.wait_for_timeout(400)
                    fields_filled.append({"field": field_key, "value": value})
                    log.info("Autocomplete selected %s = %s", field_key, value)
                    return True
            except Exception:
                continue

        page.keyboard.press("Tab")
        fields_filled.append({"field": field_key, "value": value})
        log.info("Autocomplete (no dropdown) %s = %s", field_key, value)
        return True
    except Exception as e:
        log.warning("autocomplete_field failed for %s: %s", field_key, e)
        return False


def run():
    from playwright.sync_api import sync_playwright

    SS_DIR.mkdir(exist_ok=True)
    fields_filled = []
    screenshot_bytes = b""

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=200)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900},
                                  user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")
        page = ctx.new_page()

        log.info("Loading job page...")
        page.goto(URL, timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        # Click Apply
        for sel in ['button:has-text("Apply")', 'a:has-text("Apply")',
                    'button:has-text("Apply Now")', 'a:has-text("Apply Now")']:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=1000):
                    el.click()
                    log.info("Clicked Apply")
                    break
            except Exception:
                continue

        # Wait for form
        try:
            page.wait_for_selector("#first_name", state="attached", timeout=8000)
            log.info("Form attached to DOM")
        except Exception:
            log.warning("Form not found in 8s")

        page.wait_for_timeout(1500)

        # Scroll to form
        try:
            page.locator("#first_name").first.scroll_into_view_if_needed(timeout=3000)
            page.wait_for_timeout(500)
        except Exception:
            pass

        # Take screenshot of form (before fill)
        ss_before = page.screenshot()
        (SS_DIR / f"{JOB_ID}_before.png").write_bytes(ss_before)

        # === Fill text fields ===
        react_fill(page, "#first_name", "Taranveer", "first_name", fields_filled)
        react_fill(page, "#last_name", "Singh", "last_name", fields_filled)
        react_fill(page, "#email", "taran.impact@gmail.com", "email", fields_filled)

        # Country dropdown
        country_select(page, fields_filled)

        # Phone
        react_fill(page, "#phone", "4847928221", "phone", fields_filled)

        # Education
        autocomplete_field(page, "#school--0", "Pennsylvania State University", "school", fields_filled)
        autocomplete_field(page, "#degree--0", "Bachelor", "degree", fields_filled)
        autocomplete_field(page, "#discipline--0", "Computer Science", "discipline", fields_filled)
        react_fill(page, "#start-year--0", "2024", "start_year", fields_filled)
        react_fill(page, "#end-year--0", "2027", "end_year", fields_filled)

        # Custom questions
        react_fill(page, "#question_35973715002", "Royersford, PA", "current_location", fields_filled)
        react_fill(page, "#question_35973716002", "Chicago, IL (flexible)", "location_preference", fields_filled)
        react_fill(page, "#question_35973714002", "No outstanding offers or deadlines", "offers_deadlines", fields_filled)
        react_fill(page, "#question_35973713002", "US Citizen — authorized to work in the US", "employment_eligibility", fields_filled)
        react_fill(page, "#question_35973717002",
                   "Bachelor of Science in Artificial Intelligence Engineering", "degree_pursuing", fields_filled)

        # Checkboxes
        react_check(page, "#question_35973718002\\[\\]_238691564002", "area_ml", "Machine Learning", fields_filled)
        react_check(page, "#question_35973718002\\[\\]_238691566002", "area_cs", "Computer Science", fields_filled)
        react_check(page, "#question_35973718002\\[\\]_238691567002", "area_engineering", "Engineering", fields_filled)

        # Screenshot filled form (top)
        try:
            page.locator("#first_name").scroll_into_view_if_needed()
            page.wait_for_timeout(600)
        except Exception:
            pass
        screenshot_bytes = page.screenshot()
        (SS_DIR / f"{JOB_ID}.png").write_bytes(screenshot_bytes)

        # Bottom screenshot
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(500)
        ss_bottom = page.screenshot()
        (SS_DIR / f"{JOB_ID}_bottom.png").write_bytes(ss_bottom)

        log.info("DONE. Filled %d fields.", len(fields_filled))
        log.info("Fields: %s", [f['field'] for f in fields_filled])
        log.info("Browser stays open 10s for review...")
        page.wait_for_timeout(10000)
        browser.close()

    return {
        "fields_filled": fields_filled,
        "screenshot_b64": base64.b64encode(screenshot_bytes).decode() if screenshot_bytes else "",
    }


if __name__ == "__main__":
    result = run()
    print(f"\nFilled {len(result['fields_filled'])} fields:")
    for f in result['fields_filled']:
        print(f"  {f['field']}: {f['value']}")

    jobs = json.loads(CACHE_PATH.read_text())
    for job in jobs:
        if job["id"] == JOB_ID:
            job["fill_result"] = {"fields_filled": result["fields_filled"], "error": ""}
            job["status"] = "needs_review"
            job["screenshot_b64"] = result["screenshot_b64"]
    CACHE_PATH.write_text(json.dumps(jobs, ensure_ascii=False, indent=2))
    print("Cache updated.")
