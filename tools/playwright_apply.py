"""
Playwright-based job application filler.
Opens the job URL in headed Chromium, fills common form fields,
takes a screenshot, and leaves the browser open for Taran to review and submit.
"""

import base64
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Taran's contact info used for form filling
CONTACT = {
    "first_name": "Taranveer",
    "last_name": "Singh",
    "full_name": "Taranveer Singh",
    "email": "taran.impact@gmail.com",
    "university": "Pennsylvania State University",
    "major": "Artificial Intelligence Engineering",
    "grad_year": "2027",
    "linkedin": "https://linkedin.com/in/taranveersingh",
}

# Field selectors — ordered most-specific to least-specific
FIELD_MAP = {
    "first_name": [
        'input[autocomplete="given-name"]',
        'input[name="first_name"]', 'input[name="firstName"]',
        'input[id*="firstName" i]', 'input[id*="first_name" i]',
        'input[placeholder*="First name" i]', 'input[aria-label*="First name" i]',
    ],
    "last_name": [
        'input[autocomplete="family-name"]',
        'input[name="last_name"]', 'input[name="lastName"]',
        'input[id*="lastName" i]', 'input[id*="last_name" i]',
        'input[placeholder*="Last name" i]', 'input[aria-label*="Last name" i]',
    ],
    "full_name": [
        'input[autocomplete="name"]',
        'input[name="name"]',
        'input[placeholder*="Full name" i]', 'input[aria-label*="Full name" i]',
    ],
    "email": [
        'input[type="email"]', 'input[autocomplete="email"]',
        'input[name="email"]', 'input[id*="email" i]',
        'input[placeholder*="Email" i]',
    ],
    "university": [
        'input[name*="school" i]', 'input[name*="university" i]',
        'input[id*="school" i]', 'input[id*="university" i]',
        'input[placeholder*="University" i]', 'input[placeholder*="School" i]',
        'input[aria-label*="School" i]', 'input[aria-label*="University" i]',
    ],
    "major": [
        'input[name*="major" i]', 'input[name*="degree" i]',
        'input[name*="field_of_study" i]',
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
}

# Module-level playwright instance so browsers stay open after the function returns
_playwright_instance = None
_open_browsers = []


def fill_application(job: dict) -> dict:
    """
    Open the job URL in a headed browser, fill what we can, screenshot, leave open.
    Returns {"fields_filled": [...], "screenshot_b64": "..."}.
    Browser stays open so Taran can review and submit.
    """
    global _playwright_instance, _open_browsers

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "error": "Playwright not installed. Run: pip install playwright && playwright install chromium",
            "fields_filled": [],
            "screenshot_b64": "",
        }

    url = job.get("url", "")
    fields_filled = []
    screenshot_b64 = ""

    try:
        if _playwright_instance is None:
            _playwright_instance = sync_playwright().start()

        browser = _playwright_instance.chromium.launch(
            headless=False,
            slow_mo=350,
            args=["--start-maximized"],
        )
        _open_browsers.append(browser)

        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        )
        page = context.new_page()

        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)

        # Try to click an "Apply" button if this is a job listing page
        for apply_sel in ['a:has-text("Apply")', 'button:has-text("Apply")', 'a:has-text("Apply Now")', 'button:has-text("Apply Now")']:
            try:
                el = page.locator(apply_sel).first
                if el.is_visible(timeout=800):
                    el.click()
                    page.wait_for_timeout(2000)
                    break
            except Exception:
                continue

        # Fill each field
        for field_key, selectors in FIELD_MAP.items():
            value = CONTACT.get(field_key, "")
            if not value:
                continue
            for selector in selectors:
                try:
                    el = page.locator(selector).first
                    if el.is_visible(timeout=600):
                        el.triple_click()
                        el.fill(value)
                        fields_filled.append({"field": field_key, "value": value})
                        log.info("Filled %s: %s", field_key, value)
                        break
                except Exception:
                    continue

        # Scroll to make form visible for screenshot
        page.evaluate("window.scrollTo(0, 200)")
        page.wait_for_timeout(800)

        # Screenshot (viewport only — faster)
        screenshot_bytes = page.screenshot()
        screenshot_b64 = base64.b64encode(screenshot_bytes).decode()

        # Save PNG to disk
        ss_dir = Path(__file__).parent.parent / "screenshots"
        ss_dir.mkdir(exist_ok=True)
        (ss_dir / f"{job.get('id', 'job')}.png").write_bytes(screenshot_bytes)

        log.info("Filled %d fields for %s — browser left open for review", len(fields_filled), job.get("company"))

    except Exception as e:
        log.exception("fill_application error for %s", url)
        return {"error": str(e), "fields_filled": fields_filled, "screenshot_b64": screenshot_b64}

    return {
        "fields_filled": fields_filled,
        "screenshot_b64": screenshot_b64,
    }
