"""
Fill remaining Aquatic Capital Management Greenhouse dropdowns.
The contact fields (name/email/phone/country) are already filled from the previous run.
This script handles the additional dropdowns and checkboxes.
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
    try:
        el = page.locator(selector).first
        el.scroll_into_view_if_needed(timeout=3000)
        el.click()
        page.wait_for_timeout(200)
        page.keyboard.press("Meta+a")
        page.keyboard.press("Backspace")
        page.keyboard.type(value, delay=50)
        page.keyboard.press("Tab")
        page.wait_for_timeout(150)
        fields_filled.append({"field": field_key, "value": value})
        log.info("Filled %s = %s", field_key, value)
        return True
    except Exception as e:
        log.warning("react_fill failed for %s: %s", field_key, e)
        return False


def react_check(page, selector, field_key, value, fields_filled):
    try:
        el = page.locator(selector).first
        el.scroll_into_view_if_needed(timeout=2000)
        el.click(force=True)
        fields_filled.append({"field": field_key, "value": value})
        log.info("Checked %s: %s", field_key, value)
        return True
    except Exception as e:
        log.warning("react_check failed for %s: %s", field_key, e)
        return False


def select_dropdown(page, trigger_selector, option_text, field_key, fields_filled):
    """Click a custom dropdown then click an option by text."""
    try:
        el = page.locator(trigger_selector).first
        el.scroll_into_view_if_needed(timeout=3000)
        el.click()
        page.wait_for_timeout(700)

        # Try clicking by text in various dropdown structures
        for opt_sel in [
            f'[role="option"]:has-text("{option_text}")',
            f'li:has-text("{option_text}")',
            f'[class*="option"]:has-text("{option_text}")',
            f'div:has-text("{option_text}")',
        ]:
            try:
                opt = page.locator(opt_sel).first
                if opt.is_visible(timeout=600):
                    opt.click()
                    page.wait_for_timeout(400)
                    fields_filled.append({"field": field_key, "value": option_text})
                    log.info("Dropdown selected %s = %s", field_key, option_text)
                    return True
            except Exception:
                continue

        # Fallback: type to search then click
        page.keyboard.type(option_text, delay=60)
        page.wait_for_timeout(600)
        for opt_sel in ['[role="option"]', 'li[role="option"]']:
            try:
                opt = page.locator(opt_sel).first
                if opt.is_visible(timeout=500):
                    opt.click()
                    fields_filled.append({"field": field_key, "value": option_text})
                    log.info("Dropdown (typed) selected %s = %s", field_key, option_text)
                    return True
            except Exception:
                continue

        page.keyboard.press("Escape")
        log.warning("Could not select dropdown %s = %s", field_key, option_text)
        return False
    except Exception as e:
        log.warning("select_dropdown failed for %s: %s", field_key, e)
        return False


def country_select(page, fields_filled):
    try:
        country_el = page.locator("#country").first
        country_el.scroll_into_view_if_needed(timeout=2000)
        country_el.click()
        page.wait_for_timeout(800)
        page.keyboard.type("United States", delay=80)
        page.wait_for_timeout(800)
        for opt_sel in ['[role="option"]', 'li[role="option"]', '[class*="option"]']:
            try:
                opt = page.locator(opt_sel).first
                if opt.is_visible(timeout=600):
                    opt.click()
                    page.wait_for_timeout(400)
                    fields_filled.append({"field": "country", "value": "United States"})
                    log.info("Selected country")
                    return True
            except Exception:
                continue
        page.keyboard.press("Enter")
        fields_filled.append({"field": "country", "value": "United States"})
        return True
    except Exception as e:
        log.warning("country_select failed: %s", e)
        return False


def autocomplete_field(page, selector, value, field_key, fields_filled):
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
                    log.info("Autocomplete %s = %s", field_key, value)
                    return True
            except Exception:
                continue
        page.keyboard.press("Tab")
        fields_filled.append({"field": field_key, "value": value})
        return True
    except Exception as e:
        log.warning("autocomplete failed %s: %s", field_key, e)
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
        for sel in ['button:has-text("Apply")', 'a:has-text("Apply")']:
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
        except Exception:
            pass
        page.wait_for_timeout(1500)

        # === Contact fields ===
        react_fill(page, "#first_name", "Taranveer", "first_name", fields_filled)
        react_fill(page, "#last_name", "Singh", "last_name", fields_filled)
        react_fill(page, "#email", "***REDACTED***", "email", fields_filled)
        country_select(page, fields_filled)
        react_fill(page, "#phone", "4847928221", "phone", fields_filled)

        # === Education ===
        autocomplete_field(page, "#school--0", "Pennsylvania State University", "school", fields_filled)
        autocomplete_field(page, "#degree--0", "Bachelor", "degree", fields_filled)
        autocomplete_field(page, "#discipline--0", "Computer Science", "discipline", fields_filled)
        react_fill(page, "#start-year--0", "2024", "start_year", fields_filled)
        react_fill(page, "#end-year--0", "2028", "end_year", fields_filled)

        # === Custom text questions ===
        react_fill(page, "#question_35973715002", "Royersford, PA", "current_location", fields_filled)
        react_fill(page, "#question_35973716002", "Chicago, IL (flexible)", "location_preference", fields_filled)
        react_fill(page, "#question_35973714002", "No outstanding offers or deadlines", "offers_deadlines", fields_filled)
        react_fill(page, "#question_35973713002", "US Citizen — authorized to work in the US", "employment_eligibility", fields_filled)
        react_fill(page, "#question_35973717002",
                   "Bachelor of Science in Artificial Intelligence Engineering", "degree_pursuing", fields_filled)

        # === Area of study checkboxes ===
        react_check(page, "#question_35973718002\\[\\]_238691564002", "area_ml", "Machine Learning", fields_filled)
        react_check(page, "#question_35973718002\\[\\]_238691566002", "area_cs", "Computer Science", fields_filled)
        react_check(page, "#question_35973718002\\[\\]_238691567002", "area_engineering", "Engineering", fields_filled)

        # === Remaining dropdown questions ===
        # Scroll down to find remaining fields
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1000)

        # Discover all unfilled dropdowns/selects on page
        remaining = page.evaluate("""
            () => Array.from(document.querySelectorAll('[class*="Select"], select')).map(el => ({
                id: el.id, class: el.className.slice(0,60),
                label: document.querySelector('label[for="' + el.id + '"]')?.textContent?.trim() || '',
                value: el.value || ''
            }))
        """)
        log.info("Remaining selects: %s", remaining[:10])

        # "What degree are you currently pursuing" — find by label
        try:
            degree_q = page.locator('label:has-text("What degree are you currently pursuing")').first
            degree_q.scroll_into_view_if_needed(timeout=2000)
            # Find the select/dropdown sibling
            parent = degree_q.locator('..')
            page.wait_for_timeout(300)
        except Exception:
            pass

        # Try selecting dropdown for degree question by Playwright text selector
        for label_text, option_val, field_key in [
            ("What degree are you currently pursuing", "Bachelor", "degree_question"),
            ("What year are you expected to graduate", "2027", "grad_year_question"),
        ]:
            try:
                # Find the label
                lbl = page.locator(f'label:has-text("{label_text[:30]}")').first
                lbl.scroll_into_view_if_needed(timeout=2000)
                # Click associated dropdown (sibling or next element)
                container = page.locator(f'text="{label_text[:30]}"').locator('..').locator('..')
                dropdown_trigger = container.locator('[role="combobox"], [class*="Select__control"], [class*="select"]').first
                dropdown_trigger.click()
                page.wait_for_timeout(600)

                for opt_sel in [f'[role="option"]:has-text("{option_val}")',
                                f'li:has-text("{option_val}")']:
                    try:
                        opt = page.locator(opt_sel).first
                        if opt.is_visible(timeout=600):
                            opt.click()
                            page.wait_for_timeout(300)
                            fields_filled.append({"field": field_key, "value": option_val})
                            log.info("Selected %s = %s", field_key, option_val)
                            break
                    except Exception:
                        continue
            except Exception as e:
                log.debug("Could not find dropdown for %s: %s", label_text[:30], e)

        # Math competitions — "I have not participated"
        try:
            no_comp = page.locator('label:has-text("I have not participated")').first
            no_comp.scroll_into_view_if_needed(timeout=2000)
            cb = page.locator('input[type="checkbox"]').filter(has=page.locator('~ label:has-text("I have not participated")')).first
            if not cb.is_visible(timeout=500):
                # Try sibling approach
                no_comp.locator('..').locator('input[type="checkbox"]').first.click(force=True)
            else:
                cb.click(force=True)
            fields_filled.append({"field": "math_competitions", "value": "I have not participated"})
            log.info("Checked: I have not participated in math competitions")
        except Exception as e:
            # Fallback: locate by text near checkbox
            try:
                page.locator('text="I have not participated in any of these competitions"').locator('..').locator('input').first.click(force=True)
                fields_filled.append({"field": "math_competitions", "value": "I have not participated"})
                log.info("Checked math no-competitions (fallback)")
            except Exception as e2:
                log.debug("Math competition checkbox: %s", e2)

        # Compensation expectations
        try:
            comp_textarea = page.locator('textarea').filter(has_text="").first
            comp_textarea.scroll_into_view_if_needed(timeout=2000)
            comp_textarea.click()
            page.keyboard.type("Competitive market rate for internship ($8,000-12,000/month)", delay=40)
            fields_filled.append({"field": "compensation", "value": "Competitive market rate"})
            log.info("Filled compensation")
        except Exception as e:
            # Try text input near compensation label
            try:
                comp_input = page.locator('label:has-text("compensation")').locator('..').locator('input, textarea').first
                comp_input.scroll_into_view_if_needed(timeout=2000)
                comp_input.click()
                page.keyboard.type("Competitive market rate for internship", delay=40)
                fields_filled.append({"field": "compensation", "value": "Competitive market rate"})
            except Exception:
                log.debug("Could not fill compensation: %s", e)

        # How did you hear about this job
        try:
            hear_input = page.locator('label:has-text("How did you hear")').locator('..').locator('input').first
            hear_input.scroll_into_view_if_needed(timeout=2000)
            hear_input.click()
            page.keyboard.type("LinkedIn", delay=50)
            fields_filled.append({"field": "referral_source", "value": "LinkedIn"})
            log.info("Filled referral source")
        except Exception as e:
            log.debug("referral source: %s", e)

        # === Final screenshots ===
        try:
            page.locator("#first_name").scroll_into_view_if_needed()
            page.wait_for_timeout(500)
        except Exception:
            pass
        screenshot_bytes = page.screenshot()
        (SS_DIR / f"{JOB_ID}.png").write_bytes(screenshot_bytes)

        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(500)
        ss_bottom = page.screenshot()
        (SS_DIR / f"{JOB_ID}_bottom.png").write_bytes(ss_bottom)

        log.info("DONE. Filled %d fields.", len(fields_filled))
        log.info("Fields: %s", [f['field'] for f in fields_filled])
        log.info("Browser staying open 10s for review...")
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
