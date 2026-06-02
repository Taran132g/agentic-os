#!/usr/bin/env python3
"""3-niche content rotation cron — built for n8n (2026-05-31).

Each run picks ONE niche (rotating by day-of-year across the 3-niche stack),
runs its pipeline's `fetch` then `render 1`, and:
  - on success → sends the finished .mp4 to Telegram (sendVideo, falls back to a
    path message if the file is over Telegram's 50MB bot limit)
  - on failure → sends a Telegram alert with the tail of the error

Niches (share content_pipeline.py renderer):
    aita    → aita_pipeline.py     (Adam voice, AITA storytime)
    stoic   → stoic_pipeline.py    (Bill voice, stoic/motivational)
    horror  → horror_pipeline.py   (Callum/Jessica, two-sentence horror)

n8n (daily cron) runs:
    python3 ~/agentic_os/content_cron.py            # auto-rotate by day
    python3 ~/agentic_os/content_cron.py stoic      # force a niche

Env:
    CONTENT_CRON_DRY=1   print the plan + chosen niche, render nothing
Exit 0 on success, 1 on render failure.
"""

import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

AGENTIC_DIR = Path(__file__).resolve().parent
load_dotenv(AGENTIC_DIR / ".env")

PIPELINES = {
    "aita":   "aita_pipeline.py",
    "stoic":  "stoic_pipeline.py",
    "horror": "horror_pipeline.py",
}
ROTATION = ["aita", "stoic", "horror"]


def _tg_api(method: str, **kwargs):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return None
    try:
        return requests.post(f"https://api.telegram.org/bot{token}/{method}",
                             timeout=120, **kwargs)
    except Exception as e:
        print("! telegram error:", e)
        return None


def _send_text(text: str) -> None:
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not chat_id:
        return
    _tg_api("sendMessage", json={"chat_id": int(chat_id), "text": text[:4000],
                                 "parse_mode": "HTML"})


def _send_video(path: Path, caption: str) -> None:
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not chat_id:
        return
    size_mb = path.stat().st_size / 1e6 if path.exists() else 0
    if path.exists() and size_mb <= 49:
        with open(path, "rb") as fh:
            r = _tg_api("sendVideo",
                        data={"chat_id": int(chat_id), "caption": caption[:1000],
                              "parse_mode": "HTML"},
                        files={"video": fh})
        if r is not None and r.ok:
            return
    # too big / send failed → just point to the file on disk
    _send_text(f"{caption}\n📁 {path}  ({size_mb:.0f}MB)")


def _pick_niche() -> str:
    if len(sys.argv) > 1 and sys.argv[1] in PIPELINES:
        return sys.argv[1]
    # deterministic rotation by day-of-year (no Math.random / Date.now needed)
    doy = datetime.now().timetuple().tm_yday
    return ROTATION[doy % len(ROTATION)]


def _run(pipeline: str, *cmd_args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", str(AGENTIC_DIR / pipeline), *cmd_args],
        cwd=str(AGENTIC_DIR), capture_output=True, text=True, timeout=900,
    )


def _parse_rendered_path(stdout: str) -> Path | None:
    # both pipelines print "Rendered: <path>" (and "Part N: <path>")
    m = re.search(r"Rendered(?:\s+\d+\s+parts)?:\s*(.+\.mp4)", stdout)
    if not m:
        m = re.search(r"Part \d+:\s*(.+\.mp4)", stdout)
    if m:
        p = Path(m.group(1).strip())
        if p.exists():
            return p
    return None


def main() -> int:
    niche = _pick_niche()
    pipeline = PIPELINES[niche]
    today = datetime.now().strftime("%a %b %d")

    if os.environ.get("CONTENT_CRON_DRY") == "1":
        print(f"[dry] {today}: niche=<{niche}> pipeline={pipeline} "
              f"→ would run `fetch` then `render 1`")
        return 0

    print(f"== content_cron {today}: niche={niche} ({pipeline}) ==")

    # Stage 1: fetch today's candidates
    fetched = _run(pipeline, "fetch")
    if fetched.returncode != 0:
        tail = (fetched.stderr or fetched.stdout)[-600:]
        _send_text(f"⚠️ <b>Content cron — {niche} fetch failed</b>\n<pre>{tail}</pre>")
        print(fetched.stdout, fetched.stderr, sep="\n")
        return 1

    # Stage 2: render the top pick
    rendered = _run(pipeline, "render", "1")
    print(rendered.stdout)
    if rendered.returncode != 0:
        tail = (rendered.stderr or rendered.stdout)[-600:]
        _send_text(f"⚠️ <b>Content cron — {niche} render failed</b>\n<pre>{tail}</pre>")
        return 1

    mp4 = _parse_rendered_path(rendered.stdout)
    caption = f"🎬 <b>{niche.upper()} drop</b> — {today}"
    if mp4:
        _send_video(mp4, caption + f"\n{mp4.name}")
        print(f"Sent video: {mp4}")
    else:
        _send_text(caption + "\n(rendered, but couldn't locate the .mp4 path — "
                   "check ~/Desktop render folders)")
        print("! render ok but no mp4 path parsed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.TimeoutExpired:
        _send_text("⚠️ <b>Content cron timed out</b> (render > 15min)")
        sys.exit(1)
    except Exception as e:
        import traceback
        traceback.print_exc()
        _send_text(f"⚠️ <b>Content cron crashed:</b> {e}")
        sys.exit(1)
