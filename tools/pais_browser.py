"""
PAIS persistent browser — one Chromium profile that PAIS reuses across runs.

Taran logs into a service ONCE in this profile (Supabase, Google, GitHub,
Pinecone, etc.). After that, every subsequent PAIS browser action against
that service is already authenticated — no re-login, no OAuth dance.

This is the foundation for autonomous dashboard automation. The PAIS agent
opens the profile via `pais_browser.py open --url ...`, takes screenshots
with `tools/computer.py screenshot`, and clicks through dashboards to do
work like "create a Supabase project, copy the URL + anon key, paste them
into ~/FindingFounders/frontend/.env.local".

Usage from a PAIS agent (Bash):

    # 1. ONE-TIME: log into a service. Opens a headed browser, you sign in,
    #    you press Enter in the terminal when done. Session is saved.
    python3 ~/agentic_os/tools/pais_browser.py bootstrap-login \
        --service supabase --url https://supabase.com/dashboard/sign-in

    # 2. Anytime after: open the persistent profile programmatically.
    python3 ~/agentic_os/tools/pais_browser.py open --url https://supabase.com/dashboard

    # 3. Drive deterministic Playwright steps via the Python API:
    from tools.pais_browser import session
    with session(headed=True) as page:
        page.goto("https://supabase.com/dashboard/projects")
        page.click("text=New project")
        ...

The profile lives at ~/agentic_os/.browser_profile/ and is a standard
Chromium user-data-dir. Cookies, localStorage, IndexedDB, extensions —
all persist across calls.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import sys
from pathlib import Path
from typing import Iterator, Optional

from playwright.sync_api import BrowserContext, Page, sync_playwright

log = logging.getLogger(__name__)

PAIS_DIR = Path(__file__).resolve().parent.parent
PROFILE_DIR = PAIS_DIR / ".browser_profile"
PROFILE_DIR.mkdir(exist_ok=True)

# A real desktop UA — Google's bot-checks are less aggressive against this
# than the default Playwright "HeadlessChrome" string.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


@contextlib.contextmanager
def session(
    headed: bool = True,
    viewport: tuple[int, int] = (1440, 900),
    slow_mo_ms: int = 0,
) -> Iterator[Page]:
    """
    Open the persistent PAIS browser profile and yield a Page.

    headed=True is the right default: Taran can watch what's happening and
    take over if a CAPTCHA / 2FA prompt appears. Set headed=False only for
    fully-deterministic background scrapes.
    """
    with sync_playwright() as p:
        ctx: BrowserContext = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=not headed,
            viewport={"width": viewport[0], "height": viewport[1]},
            user_agent=USER_AGENT,
            slow_mo=slow_mo_ms,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            yield page
        finally:
            ctx.close()


def bootstrap_login(service: str, url: str, ready_url_substring: Optional[str] = None) -> int:
    """
    Open a headed browser, navigate to `url`, and wait for Taran to log in.

    Detects completion in one of two ways:
      1. If `ready_url_substring` is given, polls page.url until it contains
         that string (e.g. "dashboard" once Supabase finishes redirecting).
      2. Otherwise, prompts in the terminal: press Enter when done.

    Returns 0 on success, 1 if Taran aborted.
    """
    print(f"\n→ Opening {service} login: {url}")
    print("  A browser window will open. Sign in normally.")
    if ready_url_substring:
        print(f"  PAIS will auto-detect completion when the URL contains "
              f"'{ready_url_substring}'.")
    else:
        print("  When you're done, come back to this terminal and press Enter.")

    with session(headed=True) as page:
        page.goto(url, wait_until="domcontentloaded")
        if ready_url_substring:
            import time
            deadline = time.time() + 600  # 10 min
            while time.time() < deadline:
                try:
                    if ready_url_substring in page.url:
                        print(f"\n✓ Detected login complete (URL → {page.url})")
                        # Give cookies a moment to settle.
                        page.wait_for_timeout(1500)
                        break
                except Exception:
                    pass
                page.wait_for_timeout(750)
            else:
                print("\n✗ Login timeout after 10 min. Session may still be partial.")
                return 1
        else:
            try:
                input("\nPress Enter here once you've finished signing in… ")
            except KeyboardInterrupt:
                print("\n✗ Aborted.")
                return 1

    print(f"✓ {service} session saved to {PROFILE_DIR}")
    return 0


def open_url(url: str, wait_seconds: int = 5) -> int:
    """Open the persistent profile to a URL and pause briefly (for inspection)."""
    with session(headed=True) as page:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(wait_seconds * 1000)
    return 0


def reset_profile() -> int:
    """Wipe the persistent profile. Use if a session goes bad."""
    import shutil
    if PROFILE_DIR.exists():
        shutil.rmtree(PROFILE_DIR)
        PROFILE_DIR.mkdir(exist_ok=True)
        print(f"✓ Reset {PROFILE_DIR}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="PAIS persistent browser")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_boot = sub.add_parser("bootstrap-login", help="One-time interactive sign-in")
    p_boot.add_argument("--service", required=True,
                        help="Short name for logs (supabase, google, pinecone, github)")
    p_boot.add_argument("--url", required=True, help="Sign-in URL")
    p_boot.add_argument("--ready", default=None,
                        help="URL substring that means login completed (optional)")

    p_open = sub.add_parser("open", help="Open the profile to a URL")
    p_open.add_argument("--url", required=True)
    p_open.add_argument("--wait", type=int, default=5)

    sub.add_parser("reset", help="Wipe the persistent profile")

    args = ap.parse_args()
    if args.cmd == "bootstrap-login":
        return bootstrap_login(args.service, args.url, args.ready)
    if args.cmd == "open":
        return open_url(args.url, args.wait)
    if args.cmd == "reset":
        return reset_profile()
    return 2


if __name__ == "__main__":
    sys.exit(main())
