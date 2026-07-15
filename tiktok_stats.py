#!/usr/bin/env python3
"""tiktok_stats.py — hourly TikTok analytics poller.

Reads the logged-in TikTok Studio content page (.browser_profile_tiktok),
snapshots per-post views, and:
  * Telegrams ONCE per post if it has <100 views after 24h (underperformer)
  * writes a rolling summary (views gained last 24h + trend vs the prior 24h)
    to ~/.pais/tiktok_stats.json — the Mac bridge serves it to the Clips agent

Skips a cycle if the TikTok browser profile is busy (a draft upload is running).
Scheduled hourly by the com.pais.tiktokstats LaunchAgent.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
PROFILE = str(BASE / ".browser_profile_tiktok")
STATE = Path.home() / ".pais" / "tiktok_stats.json"
UNDERPERFORM_VIEWS = 100
UNDERPERFORM_AGE_H = 24
KEEP_SNAPSHOTS_H = 80

_NUM_RE = re.compile(r"^(\d[\d,.]*)([KMB]?)$", re.I)
_DATE_RE = re.compile(r"^[A-Z][a-z]{2} \d{1,2}(?:, \d{4})?, \d{1,2}:\d{2} [AP]M$")
_DUR_RE = re.compile(r"^\d{1,2}:\d{2}$")


def _n(s: str) -> int | None:
    m = _NUM_RE.match(s.strip())
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    return int(v * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2).upper()])


def _telegram(text: str) -> None:
    try:
        from campaign_watch import telegram, _load_env
        _load_env()
        telegram(text)
    except Exception as e:  # noqa: BLE001
        print(f"telegram failed: {e}\n{text}")


def fetch_posts() -> list[dict]:
    """TikTok Studio posts list → [{key, caption, posted, views}]."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=True, viewport={"width": 1380, "height": 2400})
        try:
            pg = ctx.new_page()
            pg.goto("https://www.tiktok.com/tiktokstudio/content",
                    timeout=60000, wait_until="domcontentloaded")
            pg.wait_for_timeout(9000)
            for _ in range(3):
                pg.mouse.wheel(0, 1800)
                pg.wait_for_timeout(1000)
            text = pg.evaluate("document.body.innerText")
        finally:
            ctx.close()
    text = text.replace(" ", " ")   # TikTok's narrow no-break space before AM/PM
    # per post: MM:SS / caption / "Jul 6, 8:10 PM" / privacy / views likes comments
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    posts, i = [], 0
    while i < len(lines):
        if _DUR_RE.match(lines[i]) and i + 4 < len(lines):
            caption = lines[i + 1]
            if _DATE_RE.match(lines[i + 2]) and lines[i + 3] in ("Everyone", "Friends", "Only you"):
                views = _n(lines[i + 4])
                if views is not None:
                    date_s = lines[i + 2]
                    if ", 20" not in date_s:                       # year omitted → current
                        date_s = date_s.replace(",", f", {datetime.now().year},", 1)
                    try:
                        posted = datetime.strptime(date_s, "%b %d, %Y, %I:%M %p").timestamp()
                    except ValueError:
                        posted = None
                    posts.append({"key": caption[:70], "caption": caption[:110],
                                  "posted": posted, "views": views})
                    i += 5
                    continue
        i += 1
    return posts


def poll() -> None:
    # don't fight the draft-upload browser for the profile
    if subprocess.run(["pgrep", "-f", "browser_profile_tiktok"],
                      capture_output=True).returncode == 0:
        print("profile busy (upload running) — skipping this cycle")
        return
    posts = fetch_posts()
    now = time.time()
    STATE.parent.mkdir(exist_ok=True)
    st = json.loads(STATE.read_text()) if STATE.exists() else {"snapshots": [], "alerted": []}

    # underperformer alerts (once per post)
    for p_ in posts:
        age_h = (now - p_["posted"]) / 3600 if p_["posted"] else None
        if (age_h is not None and age_h >= UNDERPERFORM_AGE_H
                and p_["views"] < UNDERPERFORM_VIEWS and p_["key"] not in st["alerted"]):
            _telegram(f"📉 Underperformer: \"{p_['caption'][:80]}\" has only "
                      f"{p_['views']} views after {age_h:.0f}h (<{UNDERPERFORM_VIEWS}). "
                      f"Consider a better hook/time slot for this campaign.")
            st["alerted"].append(p_["key"])

    st["snapshots"].append({"ts": now, "total": sum(p_["views"] for p_ in posts),
                            "posts": {p_["key"]: p_["views"] for p_ in posts}})
    st["snapshots"] = [s for s in st["snapshots"] if now - s["ts"] < KEEP_SNAPSHOTS_H * 3600]

    def _total_at(hours_ago: float) -> int | None:
        target = now - hours_ago * 3600
        cands = [s for s in st["snapshots"] if s["ts"] <= target]
        return cands[-1]["total"] if cands else None

    total = st["snapshots"][-1]["total"]
    t24 = _total_at(24)
    t48 = _total_at(48)
    views_24h = (total - t24) if t24 is not None else None
    prev_24h = (t24 - t48) if (t24 is not None and t48 is not None) else None
    st["summary"] = {"total": total, "posts": len(posts),
                     "views_24h": views_24h, "prev_24h": prev_24h,
                     "updated": now}
    _maybe_voice(st["summary"], st, now)
    STATE.write_text(json.dumps(st, indent=1))
    print(f"{len(posts)} posts · total {total} views · last24h "
          f"{views_24h if views_24h is not None else '(need 24h history)'}")


VOICE_MP3 = Path.home() / ".pais" / "clips_voice.mp3"
VOICE_MIN_GAP_H = 6      # ElevenLabs quota guard — regenerate at most every 6h


def _maybe_voice(s: dict, st: dict, now: float) -> None:
    """Short Adam (ElevenLabs) line with the 24h numbers — played when Taran
    opens the Clips agent. Regenerated at most every 6h to protect TTS quota."""
    if now - st.get("voice_ts", 0) < VOICE_MIN_GAP_H * 3600 and VOICE_MP3.exists():
        return
    if s["views_24h"] is not None:
        trend = ("up from" if s["views_24h"] >= (s["prev_24h"] or 0) else "down from")
        line = (f"Hey Taran. Your clips pulled {s['views_24h']:,} views in the last "
                f"24 hours, {trend} {s['prev_24h']:,} the day before. "
                f"Total is {s['total']:,}." if s["prev_24h"] is not None else
                f"Hey Taran. Your clips pulled {s['views_24h']:,} views in the last "
                f"24 hours. Total is {s['total']:,}.")
    else:
        line = (f"Hey Taran. Analytics are warming up — {s['total']:,} total views "
                f"across {s['posts']} posts so far. Daily numbers land tomorrow.")
    try:
        from content_pipeline import generate_voiceover
        generate_voiceover(line, VOICE_MP3, "adam")
        st["voice_ts"] = now
        print(f"🔊 voice line regenerated: {line[:70]}…")
    except Exception as e:  # noqa: BLE001 — stats must survive TTS failures
        print(f"voice generation skipped: {e}")


if __name__ == "__main__":
    poll()
