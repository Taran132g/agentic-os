#!/usr/bin/env python3
"""campaign_watch.py — hourly clipping-campaign watcher.

Polls the clipping platforms for campaign cards, diffs against the last
snapshot, and Telegrams Taran when NEW campaigns appear (name + rate + where).
Runs headless via Playwright. Scheduled hourly by the com.pais.campaignwatch
LaunchAgent.

Sources (edit WATCH_SOURCES to add more):
  * Whop Content Rewards discover  — public, no login
  * Vyro campaigns                 — needs a one-time login (see bootstrap)
  * TJR's whop                     — public card view of the campaign he joined

Uses its OWN Chromium profile (.browser_profile_watch) so it can never collide
with the job-fill worker's profile (Chromium ProcessSingleton).

CLI:
    python3 campaign_watch.py            # poll once, notify new (first run seeds silently)
    python3 campaign_watch.py bootstrap  # opens a visible browser to log into Vyro once
    python3 campaign_watch.py status     # print the current snapshot
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
PROFILE = str(BASE / ".browser_profile_watch")
STATE = Path.home() / ".pais" / "campaign_watch.json"

WATCH_SOURCES = {
    # public aggregator of Whop Content Rewards campaigns — no login needed
    "Content Rewards": "https://contentrewards.com/discover",
    # Clipping (clipping.net) — Speed/MrBeast/Plaqueboymax's platform
    "Clipping.net": "https://clipping.net/campaigns",
    # these two render fully only after a one-time `bootstrap` login
    "Whop discover": "https://whop.com/discover/content-rewards/",
    "Vyro": "https://app.vyro.com/",
    "TJR (joined)": "https://whop.com/tjr/",
}

# a campaign line has money + a per-view unit somewhere nearby
RATE_RE = re.compile(r"\$\s?\d[\d,.]*\s*(?:/|per)\s*(?:1k|1,?000)", re.I)
MONEY_RE = re.compile(r"\$\s?\d[\d,.]*")


def _load_env() -> None:
    env = BASE / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def telegram(text: str) -> None:
    import urllib.request
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        print("⚠ telegram creds missing — printing instead:\n" + text)
        return
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{tok}/sendMessage",
        data=json.dumps({"chat_id": chat, "text": text,
                         "disable_web_page_preview": True}).encode(),
        headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20).read()


def page_text(url: str, headless: bool = True) -> str:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"),
            viewport={"width": 1280, "height": 2000})
        try:
            page = ctx.new_page()
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(6000)          # let the SPA hydrate
            for _ in range(3):                    # nudge lazy-loaded cards
                page.mouse.wheel(0, 1500)
                page.wait_for_timeout(1200)
            return page.evaluate("document.body.innerText") or ""
        finally:
            ctx.close()


_NOISE_RE = re.compile(
    r"^(·|—|-|\d+[dhm]|join campaign|featured|entertainment|product|personal brand|"
    r"music|gaming|sports|education|apps?|software|crypto|finance|lifestyle|fitness|"
    r"other|content|status|category|clear|sign in|for enterprise|api|discover.*|"
    r"install app.*|paid out.*|budget.*|views|per .*|joined.*|⌘k)$", re.I)


def extract_campaigns(text: str) -> list[str]:
    """innerText → 'Campaign name — $rate' entries.

    Card DOM order is: header · NAME · description · [Join Campaign] · stats
    (paid/budget, submissions, $rate/1K). Splitting on 'Join Campaign' therefore
    yields chunks of [stats of card k] + [header+name+desc of card k+1] — so the
    rate at the TOP of chunk k+1 pairs with the NAME found in chunk k."""
    chunks = re.split(r"join campaign", text, flags=re.I)
    names: list[str] = []
    rates: list[str | None] = []
    for ch in chunks:
        lines = [l.strip() for l in ch.splitlines() if l.strip()]
        rate = None
        for line in lines[:6]:                      # stats block sits at the top
            m = RATE_RE.search(line)
            if m:
                rate = m.group(0)
                break
        cands = [l for l in lines
                 if len(l) <= 90 and len(re.sub(r"[^A-Za-z]", "", l)) >= 4
                 and not RATE_RE.search(l) and not _NOISE_RE.match(l)]
        names.append(max(cands, key=len)[:70] if cands else "")
        rates.append(rate)
    found, seen = [], set()
    for k in range(len(chunks) - 1):
        name, rate = names[k], rates[k + 1]
        if not name or not rate:
            continue
        key = f"{name} — {rate}"
        if key.lower() not in seen:
            seen.add(key.lower())
            found.append(key)
    return found


def poll() -> None:
    _load_env()
    STATE.parent.mkdir(exist_ok=True)
    prev = json.loads(STATE.read_text()) if STATE.exists() else None
    snap: dict[str, list[str]] = {}
    errors: list[str] = []
    for src, url in WATCH_SOURCES.items():
        try:
            snap[src] = extract_campaigns(page_text(url))
            print(f"{src}: {len(snap[src])} campaign(s)")
        except Exception as e:  # noqa: BLE001 — one dead source shouldn't kill the poll
            errors.append(f"{src}: {str(e)[:80]}")
            snap[src] = (prev or {}).get(src, [])
    # cumulative memory: lazy-loading shows different subsets per poll, so a
    # campaign only counts as NEW if it has never been seen on that source.
    merged = {src: sorted({*(prev or {}).get(src, []), *camps})
              for src, camps in snap.items()}
    STATE.write_text(json.dumps(merged, indent=2))

    if prev is None:
        print("First run — snapshot seeded, no notifications.")
        return
    news = []
    for src, camps in snap.items():
        old = {c.lower() for c in prev.get(src, [])}
        for c in camps:
            if c.lower() not in old:
                news.append(f"• [{src}] {c}")
    if news:
        telegram("🎬 New clipping campaign(s) spotted:\n" + "\n".join(news[:15])
                 + "\n\nJoin before posting — views only count after you're in."
                 + f"\n{WATCH_SOURCES['Whop Content Rewards']}")
        print(f"notified {len(news)} new")
    else:
        print("no new campaigns")
    if errors:
        print("errors: " + "; ".join(errors))


def bootstrap() -> None:
    """Open a VISIBLE browser on Vyro to log in once; cookies persist in the
    watcher's own profile."""
    print("Log into Vyro in the window that opens, then close it.")
    page_text("https://app.vyro.com/", headless=False)


def status() -> None:
    if STATE.exists():
        print(STATE.read_text())
    else:
        print("no snapshot yet — run a poll first")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "poll"
    {"poll": poll, "bootstrap": bootstrap, "status": status}.get(cmd, poll)()
