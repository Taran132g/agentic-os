#!/usr/bin/env python3
"""Fetch B-roll clips per the config in broll_sources.json.

Usage:
    python3 fetch_broll.py <category>          Download all entries for one category
    python3 fetch_broll.py <category> --force  Re-download even if files exist
    python3 fetch_broll.py --list              List all configured categories
    python3 fetch_broll.py --all               Fetch all categories (heavy!)

Edit broll_sources.json to change what gets pulled. Each category supports:
  - urls:     list of direct YouTube links
  - queries:  list of yt-dlp search strings (each pulls 1 best match)
  - duration_min / duration_max: filter in seconds
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path.home() / "agentic_os"
CONFIG = ROOT / "broll_sources.json"
CACHE  = ROOT / "broll_cache"


def slugify(s: str, maxlen: int = 50) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    return s[:maxlen]


def fetch_url(cat: str, url: str, dur_min: int, dur_max: int) -> bool:
    cat_dir = CACHE / cat
    cat_dir.mkdir(parents=True, exist_ok=True)
    # Identifier from URL — handle /shorts/ID, ?v=ID, /watch/ID, etc.
    if "/shorts/" in url:
        ident = url.split("/shorts/")[-1].split("?")[0].split("/")[0][:11]
    elif "v=" in url:
        ident = url.split("v=")[-1].split("&")[0][:11]
    else:
        ident = url.rstrip("/").split("/")[-1][:11]
    ident = re.sub(r"[^a-zA-Z0-9_-]", "_", ident) or "url"
    fname = f"{cat}_url_{ident}"
    if list(cat_dir.glob(f"{fname}.*")):
        print(f"  ✓ already cached: {fname}")
        return True
    print(f"  ↓ URL: {url}")
    cmd = [
        "yt-dlp", url,
        "--match-filter", f"duration > {dur_min} & duration < {dur_max}",
        "-f", "bestvideo[height>=720][ext=mp4]+bestaudio/best[ext=mp4]/best",
        "--merge-output-format", "mp4", "--no-warnings",
        "-o", str(cat_dir / f"{fname}.%(ext)s"),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    saved = list(cat_dir.glob(f"{fname}.*"))
    if saved:
        print(f"    ✓ {saved[0].name}")
        return True
    print(f"    ✗ failed: {r.stderr[-200:] if r.stderr else 'no stderr'}")
    return False


def fetch_channel(cat: str, chan_url: str, count: int, dur_min: int, dur_max: int) -> int:
    """Download up to N items from a YouTube channel/playlist URL.

    Channel pages (e.g. https://www.youtube.com/@User/shorts) act as playlists;
    yt-dlp pulls the most recent items first.
    """
    cat_dir = CACHE / cat
    cat_dir.mkdir(parents=True, exist_ok=True)
    print(f"  ↓ channel: {chan_url} (target {count})")
    cmd = [
        "yt-dlp", chan_url,
        "--match-filter", f"duration > {dur_min} & duration < {dur_max}",
        "-f", "bestvideo[height>=720][ext=mp4]+bestaudio/best[ext=mp4]/best",
        "--merge-output-format", "mp4", "--no-warnings",
        "--playlist-end", str(count),
        "-o", str(cat_dir / f"{cat}_chan_%(autonumber)s_%(id)s.%(ext)s"),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    saved = list(cat_dir.glob(f"{cat}_chan_*"))
    if saved:
        print(f"    ✓ pulled {len(saved)} clips")
    else:
        print(f"    ✗ failed: {r.stderr[-300:] if r.stderr else 'no stderr'}")
    return len(saved)


def fetch_query(cat: str, query: str, dur_min: int, dur_max: int) -> bool:
    cat_dir = CACHE / cat
    cat_dir.mkdir(parents=True, exist_ok=True)
    slug = slugify(query)
    fname = f"{cat}_q_{slug}"
    if list(cat_dir.glob(f"{fname}.*")):
        print(f"  ✓ already cached: {fname}")
        return True
    print(f"  ↓ query: {query!r}")
    cmd = [
        "yt-dlp", f"ytsearch3:{query}",
        "--match-filter", f"duration > {dur_min} & duration < {dur_max}",
        "-f", "bestvideo[height>=720][ext=mp4]+bestaudio/best[ext=mp4]/best",
        "--merge-output-format", "mp4", "--max-downloads", "1", "--no-warnings",
        "-o", str(cat_dir / f"{fname}.%(ext)s"),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    saved = list(cat_dir.glob(f"{fname}.*"))
    if saved:
        print(f"    ✓ {saved[0].name}")
        return True
    print(f"    ✗ no match found (try a different query)")
    return False


def fetch_category(cat: str, spec: dict, force: bool = False) -> int:
    print(f"\n=== {cat} ===")
    if force:
        # Wipe existing to force re-download
        cat_dir = CACHE / cat
        if cat_dir.exists():
            for f in cat_dir.glob("*"):
                f.unlink()
            print(f"  (force: wiped {cat_dir})")
    dur_min = spec.get("duration_min", 60)
    dur_max = spec.get("duration_max", 1800)
    saved = 0
    for url in spec.get("urls", []):
        if fetch_url(cat, url, dur_min, dur_max):
            saved += 1
    for chan in spec.get("channels", []):
        # channel entry: {"url": "...", "count": N} OR just a URL string
        if isinstance(chan, str):
            chan_url, count = chan, 10
        else:
            chan_url, count = chan["url"], chan.get("count", 10)
        saved += fetch_channel(cat, chan_url, count, dur_min, dur_max)
    for query in spec.get("queries", []):
        if fetch_query(cat, query, dur_min, dur_max):
            saved += 1
    return saved


def cmd_list():
    cfg = json.loads(CONFIG.read_text())
    print(f"{len(cfg) - 1} categories in {CONFIG.name}:\n")
    for cat, spec in cfg.items():
        if cat.startswith("_"):
            continue
        n_urls = len(spec.get("urls", []))
        n_q = len(spec.get("queries", []))
        cat_dir = CACHE / cat
        cached = len(list(cat_dir.glob("*.mp4"))) if cat_dir.exists() else 0
        print(f"  {cat:25s} | cached={cached:>2} | urls={n_urls} queries={n_q}")


def main():
    if not CONFIG.exists():
        print(f"Config missing: {CONFIG}")
        sys.exit(1)
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    arg = sys.argv[1]
    cfg = json.loads(CONFIG.read_text())

    if arg == "--list":
        cmd_list()
        return

    force = "--force" in sys.argv
    if arg == "--all":
        for cat, spec in cfg.items():
            if cat.startswith("_"):
                continue
            fetch_category(cat, spec, force=force)
        return

    if arg not in cfg or arg.startswith("_"):
        print(f"Unknown category: {arg}")
        print(f"Available: {', '.join(c for c in cfg if not c.startswith('_'))}")
        sys.exit(1)
    fetch_category(arg, cfg[arg], force=force)


if __name__ == "__main__":
    main()
