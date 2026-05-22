#!/usr/bin/env python3
"""Build 10 B-roll + music template previews for visual variety testing.

Each preview is a ~25s silent-narration clip with just B-roll footage + background
music. Taran reviews them in Telegram and approves favorites for the cycle pool.

Output: ~/Desktop/AITA Renders/Templates/template_NN_<label>.mp4
Sent to Telegram with caption identifying the broll + music combo.
"""
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote_plus

import requests

HOME           = Path.home()
AGENTIC_OS     = HOME / "agentic_os"
BROLL_CACHE    = AGENTIC_OS / "broll_cache"
MUSIC_CACHE    = AGENTIC_OS / "music_cache"
OUT_DIR        = HOME / "Desktop" / "AITA Renders" / "Templates"
PREVIEW_SECS   = 25

# (broll_dir_name, yt-dlp search query)
BROLL_SOURCES = {
    "parkour":      "minecraft parkour 10 minutes no commentary",
    "subway":       "subway surfers gameplay 10 minutes no commentary",
    "gta_driving":  "gta 5 driving first person 10 minutes no commentary",
    "asmr_slime":   "satisfying slime asmr compilation no talking",
    "fortnite":     "fortnite gameplay 10 minutes no commentary",
    "cooking":      "cooking food prep top down asmr",
}

# (music_label, yt-dlp search query)  — existing entries left as None to skip download
MUSIC_SOURCES = {
    "suspenseful_storytime_lofi": None,
    "sad_piano_lofi":             None,
    "emotional_piano":            None,
    "melancholic_piano":          None,
    "dark_cinematic_piano":       None,
    "lofi_hip_hop_chill":         "lofi hip hop chill no copyright",
    "ambient_synth_dark":         "dark ambient synth pad no copyright",
    "upbeat_lofi":                "upbeat lofi beat no copyright",
    "soft_strings_emotional":     "soft emotional strings instrumental no copyright",
    "tense_thriller_pads":        "tense thriller pad music no copyright",
}

# 10 (template_id, broll_dir, music_label) combinations
TEMPLATES = [
    (1,  "parkour",     "suspenseful_storytime_lofi"),
    (2,  "parkour",     "lofi_hip_hop_chill"),
    (3,  "subway",      "sad_piano_lofi"),
    (4,  "subway",      "upbeat_lofi"),
    (5,  "gta_driving", "dark_cinematic_piano"),
    (6,  "gta_driving", "tense_thriller_pads"),
    (7,  "asmr_slime",  "ambient_synth_dark"),
    (8,  "asmr_slime",  "soft_strings_emotional"),
    (9,  "fortnite",    "emotional_piano"),
    (10, "cooking",     "melancholic_piano"),
]


def log(msg):
    print(msg, flush=True)


def download_broll(category: str, query: str) -> bool:
    """Download a single B-roll clip for the category if none cached."""
    cat_dir = BROLL_CACHE / category
    cat_dir.mkdir(parents=True, exist_ok=True)
    existing = list(cat_dir.glob("*.mp4"))
    if existing:
        log(f"  ✓ B-roll '{category}' already has {len(existing)} clip(s)")
        return True
    log(f"  ↓ downloading B-roll '{category}': {query!r}...")
    cmd = [
        "yt-dlp",
        f"ytsearch1:{query}",
        "--match-filter", "duration > 240 & duration < 1500",
        "-f", "bestvideo[height>=720][ext=mp4]+bestaudio/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--max-downloads", "1",
        "--no-warnings",
        "-o", str(cat_dir / f"{category}_%(autonumber)s.%(ext)s"),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    success = bool(list(cat_dir.glob("*.mp4")))
    if success:
        log(f"    ✓ saved to {cat_dir}")
    else:
        log(f"    ✗ failed (stderr: {r.stderr[-200:] if r.stderr else 'empty'})")
    return success


def download_music(label: str, query: str) -> bool:
    """Download a single music track if not cached."""
    MUSIC_CACHE.mkdir(parents=True, exist_ok=True)
    target = MUSIC_CACHE / f"{label}.mp3"
    if target.exists():
        log(f"  ✓ Music '{label}' already cached")
        return True
    log(f"  ↓ downloading music '{label}': {query!r}...")
    cmd = [
        "yt-dlp",
        f"ytsearch1:{query}",
        "--match-filter", "duration > 60 & duration < 600",
        "-x", "--audio-format", "mp3",
        "--max-downloads", "1",
        "--no-warnings",
        "-o", str(MUSIC_CACHE / f"{label}.%(ext)s"),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    success = target.exists()
    if success:
        log(f"    ✓ saved {target.name}")
    else:
        log(f"    ✗ failed (stderr: {r.stderr[-200:] if r.stderr else 'empty'})")
    return success


def get_random_broll_slice(broll_path: Path, out_path: Path) -> bool:
    """Pick a random PREVIEW_SECS slice of the broll video, no audio."""
    # Get duration
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(broll_path)],
        capture_output=True, text=True,
    )
    try:
        dur = float(r.stdout.strip())
    except (ValueError, AttributeError):
        return False
    if dur < PREVIEW_SECS + 5:
        return False
    start = random.uniform(2, dur - PREVIEW_SECS - 2)
    # Re-encode to portrait 1080x1920, no audio
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start:.2f}", "-i", str(broll_path),
        "-t", f"{PREVIEW_SECS}",
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
        "-an", "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return out_path.exists()


def build_preview(template_id: int, broll_dir: str, music_label: str, tmp_dir: Path) -> Path | None:
    """Render one preview: pick broll slice → apply color grade + music mix."""
    label = f"{template_id:02d}_{broll_dir}_{music_label}"
    log(f"\n[Template {template_id:02d}] {broll_dir} + {music_label}")

    broll_clips = list((BROLL_CACHE / broll_dir).glob("*.mp4"))
    music_path = MUSIC_CACHE / f"{music_label}.mp3"
    if not broll_clips or not music_path.exists():
        log(f"  ✗ missing assets — skipping")
        return None

    broll_path = random.choice(broll_clips)
    silent_slice = tmp_dir / f"{label}_slice.mp4"
    if not get_random_broll_slice(broll_path, silent_slice):
        log(f"  ✗ slicing failed")
        return None

    out_path = OUT_DIR / f"template_{label}.mp4"
    # Compose: warm grade on video + music underneath
    cmd = [
        "ffmpeg", "-y",
        "-i", str(silent_slice),
        "-i", str(music_path),
        "-t", f"{PREVIEW_SECS}",
        "-filter_complex",
        # warm storytime grade (matches content_pipeline)
        "[0:v]eq=saturation=0.88:contrast=1.05:brightness=0.02,colorbalance=rs=0.05:gs=0.0:bs=-0.05[v];"
        "[1:a]volume=0.55,aloop=loop=-1:size=2e9[a]",
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-shortest",
        "-movflags", "+faststart",
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    silent_slice.unlink(missing_ok=True)
    if out_path.exists() and out_path.stat().st_size > 100_000:
        log(f"  ✓ rendered: {out_path.name} ({out_path.stat().st_size // 1024} KB)")
        return out_path
    log(f"  ✗ render failed (stderr: {r.stderr[-300:] if r.stderr else 'empty'})")
    return None


def send_to_telegram(mp4: Path, caption: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log("  ✗ TELEGRAM_BOT_TOKEN/CHAT_ID not set")
        return False
    url = f"https://api.telegram.org/bot{token}/sendVideo"
    with open(mp4, "rb") as f:
        files = {"video": f}
        data = {"chat_id": chat_id, "caption": caption, "supports_streaming": "true"}
        r = requests.post(url, files=files, data=data, timeout=120)
    ok = r.ok and r.json().get("ok", False)
    if not ok:
        log(f"  ✗ Telegram send failed: HTTP {r.status_code} — {r.text[:200]}")
    return ok


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    log("=" * 60)
    log("STAGE 1 — ensure B-roll assets")
    log("=" * 60)
    for category, query in BROLL_SOURCES.items():
        download_broll(category, query)

    log("\n" + "=" * 60)
    log("STAGE 2 — ensure music assets")
    log("=" * 60)
    for label, query in MUSIC_SOURCES.items():
        if query is None:
            log(f"  ✓ Music '{label}' is existing (skip download)")
            continue
        download_music(label, query)

    log("\n" + "=" * 60)
    log("STAGE 3 — render 10 previews + send to Telegram")
    log("=" * 60)
    tmp_dir = Path("/tmp") / "template_previews"
    tmp_dir.mkdir(exist_ok=True)
    sent = 0
    for template_id, broll_dir, music_label in TEMPLATES:
        out = build_preview(template_id, broll_dir, music_label, tmp_dir)
        if out is None:
            continue
        caption = (
            f"🎬 Template #{template_id:02d}\n"
            f"B-roll: {broll_dir}\n"
            f"Music: {music_label}\n\n"
            f"React 👍 to approve, 👎 to skip"
        )
        if send_to_telegram(out, caption):
            sent += 1
            log(f"  ✓ sent to Telegram")
    shutil.rmtree(tmp_dir, ignore_errors=True)

    log(f"\n=== DONE — {sent}/{len(TEMPLATES)} templates sent to Telegram ===")


if __name__ == "__main__":
    main()
