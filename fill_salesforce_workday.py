"""
Salesforce Workday application filler.
URL: https://salesforce.wd12.myworkdayjobs.com/en-US/Futureforce_Internships/job/Summer-2027-Intern---Software-Engineer_JR340771

Workday uses heavy React — standard CSS selectors don't work.
We rely on data-automation-id attributes and aria-labels.
"""
import base64
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

URL = "https://salesforce.wd12.myworkdayjobs.com/en-US/Futureforce_Internships/job/Summer-2027-Intern---Software-Engineer_JR340771"
JOB_ID = "job_4"
CACHE_PATH = Path(__file__).parent / "career_jobs_cache.json"
SS_DIR = Path(__file__).parent / "screenshots"

CONTACT = {
    "first_name": "Taranveer",
    "last_name": "Singh",
    "email": "taran.impact@gmail.com",
    "phone": "4847928221",
    "city": "Royersford",
    "state": "PA",
    "zip": "19468",
    "country": "United States",
    "university": "Pennsylvania State University",
    "major": "Artificial Intelligence Engineering",
    "gpa": "3.1",
    "grad_year": "2028",
    "linkedin": "https://linkedin.com/in/taranveer-singh",
    "github": "https://github.com/Taran132g",
}

COVER_HOOK = (
    "Having shipped OOP-driven APIs, led engineering teams with AI dev tools like Claude Code, "
    "and built algorithmic systems managing real capital, I'm eager to bring that hands-on "
    "engineering depth to Salesforce's platform internship — where software directly powers "
    "how millions of businesses operate."
)


def wd_fill(page, automation_id, value, field_key, fields_filled):
    """Fill a Workday input by data-automation-id."""
    selectors = [
        f'[data-automation-id="{automation_id}"] input',
        f'[data-automation-id="{automation_id}"]',
    ]
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=2000):
                el.scroll_into_view_if_needed(timeout=2000)
                el.triple_click()
                el.fill(value)
                fields_filled.append({"field": field_key, "value": value})
                log.info("Filled %s = %s", field_key, value)
                return True
        except Exception:
            continue
    log.warning("Could not fill %s", field_key)
    return False


def wd_select(page, automation_id, option_text, field_key, fields_filled):
    """Open a Workday dropdown and pick an option by text."""
    try:
        trigger = page.locator(f'[data-automation-id="{automation_id}"]').first
        trigger.scroll_into_view_if_needed(timeout=2000)
        trigger.click()
        page.wait_for_timeout(800)

        for opt_sel in [
            f'[data-automation-id="promptOption"]:has-text("{option_text}")',
            f'[role="option"]:has-text("{option_text}")',
            f'li:has-text("{option_text}")',
        ]:
            try:
                opt = page.locator(opt_sel).first
                if opt.is_visible(timeout=800):
                    opt.click()
                    page.wait_for_timeout(400)
                    fields_filled.append({"field": field_key, "value": option_text})
                    log.info("Selected %s = %s", field_key, option_text)
                    return True
            except Exception:
                continue

        # Fallback: type and pick first suggestion
        page.keyboard.type(option_text[:10], delay=60)
        page.wait_for_timeout(1000)
        try:
            opt = page.locator('[data-automation-id="promptOption"]').first
            if opt.is_visible(timeout=800):
                opt.click()
                fields_filled.append({"field": field_key, "value": option_text})
                return True
        except Exception:
            pass

        page.keyboard.press("Escape")
        return False
    except Exception as e:
        log.warning("wd_select failed %s: %s", field_key, e)
        return False


def click_apply(page):
    """Click the Apply button on the job listing page."""
    for sel in [
        '[data-automation-id="applyButton"]',
        'button:has-text("Apply")',
        'a:has-text("Apply")',
        'button:has-text("Apply Now")',
    ]:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=2000):
                el.click()
                log.info("Clicked Apply via: %s", sel)
                page.wait_for_timeout(3000)
                return True
        except Exception:
            continue
    return False


def handle_start_application_modal(page):
    """Handle the Workday 'Start Your Application' modal.

    Workday shows a modal with: Autofill with Resume | Apply Manually | Use My Last Application
    We click 'Apply Manually' which leads to the sign-in / create-account page.
    """
    modal_selectors = [
        'button:has-text("Apply Manually")',
        'a:has-text("Apply Manually")',
        'button:has-text("Apply as Guest")',
        'a:has-text("Apply as Guest")',
        '[data-automation-id="createAccountLink"]',
        'button:has-text("Create Account")',
    ]
    for sel in modal_selectors:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=2000):
                el.click()
                log.info("Modal: clicked via %s", sel)
                page.wait_for_timeout(4000)
                return True
        except Exception:
            continue
    return False


def handle_workday_signin(page, email, fields_filled):
    """After 'Apply Manually', Workday shows a sign-in / create account screen.
    Try to fill email + create account, or detect if sign-in is needed.
    """
    # Try create account flow — fill email field
    for sel in [
        'input[data-automation-id="email"]',
        'input[type="email"]',
        'input[placeholder*="email" i]',
        'input[name="email"]',
    ]:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=2000):
                el.triple_click()
                el.fill(email)
                fields_filled.append({"field": "email_account", "value": email})
                log.info("Filled sign-in email: %s", email)
                page.wait_for_timeout(500)
                break
        except Exception:
            continue

    # Click "Create Account" or "Sign In" or "Continue"
    for btn_sel in [
        'button:has-text("Create Account")',
        'button:has-text("Continue")',
        'button:has-text("Sign In")',
        '[data-automation-id="createAccountSubmitButton"]',
        '[data-automation-id="signInSubmitButton"]',
    ]:
        try:
            btn = page.locator(btn_sel).first
            if btn.is_visible(timeout=1500):
                btn.click()
                log.info("Clicked account button: %s", btn_sel)
                page.wait_for_timeout(3000)
                return True
        except Exception:
            continue
    return False


def run():
    from playwright.sync_api import sync_playwright

    SS_DIR.mkdir(exist_ok=True)
    fields_filled = []
    screenshot_bytes = b""
    error = ""

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            slow_mo=250,
            args=["--start-maximized"],
        )
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        page = ctx.new_page()

        try:
            log.info("Loading Salesforce Workday page...")
            page.goto(URL, timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            # Step 1: Click Apply on the listing page
            click_apply(page)
            page.wait_for_timeout(2500)

            # Step 2: Handle the "Start Your Application" modal → click "Apply Manually"
            handle_start_application_modal(page)
            page.wait_for_timeout(3000)

            # Screenshot where we landed after the modal
            screenshot_bytes = page.screenshot()
            (SS_DIR / f"{JOB_ID}_state.png").write_bytes(screenshot_bytes)
            log.info("Screenshot saved: job_4_state.png")

            # Step 3: Handle sign-in / create account screen
            handle_workday_signin(page, CONTACT["email"], fields_filled)
            page.wait_for_timeout(3000)

            # Screenshot after sign-in attempt
            ss_after_login = page.screenshot()
            (SS_DIR / f"{JOB_ID}_after_login.png").write_bytes(ss_after_login)
            log.info("Screenshot saved: job_4_after_login.png")

            # Try to fill contact fields — Workday standard automation-ids
            contact_fields = [
                ("legalNameSection_firstName", CONTACT["first_name"], "first_name"),
                ("legalNameSection_lastName", CONTACT["last_name"], "last_name"),
                ("email", CONTACT["email"], "email"),
                ("phone-number", CONTACT["phone"], "phone"),
                ("addressSection_city", CONTACT["city"], "city"),
                ("addressSection_postalCode", CONTACT["zip"], "zip"),
            ]
            for automation_id, value, key in contact_fields:
                wd_fill(page, automation_id, value, key, fields_filled)
                page.wait_for_timeout(300)

            # Country dropdown
            wd_select(page, "countryDropdown", "United States", "country", fields_filled)

            # LinkedIn / website fields
            for sel in [
                'input[placeholder*="LinkedIn" i]',
                'input[aria-label*="LinkedIn" i]',
                'input[name*="linkedin" i]',
            ]:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=800):
                        el.triple_click()
                        el.fill(CONTACT["linkedin"])
                        fields_filled.append({"field": "linkedin", "value": CONTACT["linkedin"]})
                        break
                except Exception:
                    continue

            # School / education fields
            wd_fill(page, "school", CONTACT["university"], "university", fields_filled)
            wd_fill(page, "gpa", CONTACT["gpa"], "gpa", fields_filled)

            # GPA fallback
            for sel in ['input[aria-label*="GPA" i]', 'input[placeholder*="GPA" i]']:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=600):
                        el.triple_click()
                        el.fill(CONTACT["gpa"])
                        fields_filled.append({"field": "gpa", "value": CONTACT["gpa"]})
                        break
                except Exception:
                    continue

            # Cover letter / additional info textarea
            for sel in [
                'textarea[data-automation-id="additionalInfo"]',
                'textarea[aria-label*="cover" i]',
                'textarea[placeholder*="cover" i]',
                'textarea',
            ]:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=800):
                        el.click()
                        el.fill(COVER_HOOK)
                        fields_filled.append({"field": "cover_letter", "value": COVER_HOOK[:60] + "..."})
                        log.info("Filled cover letter textarea")
                        break
                except Exception:
                    continue

            # EEO fields
            # Gender
            wd_select(page, "gender", "Male", "gender", fields_filled)
            # Ethnicity / Race — Workday uses "Asian"
            for opt in ["Asian", "Asian or Pacific Islander"]:
                try:
                    wd_select(page, "ethnicity", opt, "ethnicity", fields_filled)
                    break
                except Exception:
                    continue
            # Veteran
            wd_select(page, "veteranStatus", "I am not a Protected Veteran", "veteran", fields_filled)
            # Disability
            wd_select(page, "disability", "No, I Don't Have a Disability", "disability", fields_filled)

            # Authorization questions (radio buttons)
            for sel in [
                'label:has-text("Yes"):near(:text("authorized to work"))',
                '[data-automation-id="workAuthorization"] label:has-text("Yes")',
            ]:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=800):
                        el.click()
                        fields_filled.append({"field": "work_authorization", "value": "Yes"})
                        break
                except Exception:
                    continue

            # Sponsorship — No
            for sel in [
                'label:has-text("No"):near(:text("sponsorship"))',
                '[data-automation-id="requireSponsorship"] label:has-text("No")',
            ]:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=800):
                        el.click()
                        fields_filled.append({"field": "visa_sponsorship", "value": "No"})
                        break
                except Exception:
                    continue

        except Exception as e:
            log.exception("Workday fill error")
            error = str(e)

        # Final screenshots
        try:
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(600)
            screenshot_bytes = page.screenshot()
            (SS_DIR / f"{JOB_ID}.png").write_bytes(screenshot_bytes)

            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(500)
            ss_bottom = page.screenshot()
            (SS_DIR / f"{JOB_ID}_bottom.png").write_bytes(ss_bottom)
        except Exception:
            pass

        log.info("DONE. Filled %d fields. Error: %s", len(fields_filled), error or "none")
        log.info("Browser staying open 60s for Taran to review...")
        try:
            page.wait_for_timeout(60000)
        except Exception:
            pass
        browser.close()

    return {"fields_filled": fields_filled, "screenshot_b64": base64.b64encode(screenshot_bytes).decode() if screenshot_bytes else "", "error": error}


if __name__ == "__main__":
    result = run()
    print(f"\nFilled {len(result['fields_filled'])} fields:")
    for f in result["fields_filled"]:
        print(f"  {f['field']}: {f['value']}")
    if result.get("error"):
        print(f"Error: {result['error']}")

    jobs = json.loads(CACHE_PATH.read_text())
    for job in jobs:
        if job["id"] == JOB_ID:
            job["fill_result"] = {"fields_filled": result["fields_filled"], "error": result.get("error", "")}
            job["status"] = "needs_review"
            job["screenshot_b64"] = result["screenshot_b64"]
    CACHE_PATH.write_text(json.dumps(jobs, ensure_ascii=False, indent=2))
    print("Cache updated.")
