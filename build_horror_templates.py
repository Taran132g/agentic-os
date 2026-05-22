#!/usr/bin/env python3
"""Build 10 horror template previews — each = (Dr.Philmen clip, music) combo.

Same idea as build_template_previews.py but for the horror niche. Each
preview is a ~25s silent-narration clip with just the B-roll + music so
Taran can approve which clip pairings work for the cycle pool.

Output: ~/Desktop/Horror Renders/Templates/template_NN_<label>.mp4
Sent to Telegram with caption identifying the clip + music.
"""
import os
import random
import shutil
import subprocess
from pathlib import Path

import requests

HOME           = Path.home()
AGENTIC_OS     = HOME / "agentic_os"
BROLL_DIR      = AGENTIC_OS / "broll_cache" / "horror_animated"
MUSIC_DIR      = AGENTIC_OS / "music_cache"
OUT_DIR        = HOME / "Desktop" / "Horror Renders" / "Templates"
PREVIEW_SECS   = 25

# Music tracks to test — user-picked philmen_bg primary, others as compare
MUSIC_TRACKS = [
    "philmen_bg",                  # USER PICK — the YouTube link they sent
    "horror_ambient_drone",
    "horror_tense_pads",
    "horror_creepy_atmospheric",
    "horror_bass_swell",
]


def log(msg): print(msg, flush=True)


def get_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


def build_preview(template_id: int, clip_path: Path, music_label: str, tmp_dir: Path) -> Path | None:
    music_path = MUSIC_DIR / f"{music_label}.mp3"
    if not music_path.exists():
        log(f"  ✗ missing music: {music_label}")
        return None

    label = f"{template_id:02d}_{clip_path.stem[:30]}_{music_label}"
    log(f"\n[Template {template_id:02d}] {clip_path.name} + {music_label}")

    # Pick a random slice of the clip
    dur = get_duration(clip_path)
    take = min(PREVIEW_SECS, dur - 1)
    start = random.uniform(0, max(0.1, dur - take - 1))

    silent_slice = tmp_dir / f"{label}_slice.mp4"
    # Vertical crop + no audio
    slice_cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start:.2f}", "-i", str(clip_path),
        "-t", f"{take:.2f}",
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
        "-an", "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
        str(silent_slice),
    ]
    r = subprocess.run(slice_cmd, capture_output=True, text=True, timeout=120)
    if not silent_slice.exists():
        log(f"  ✗ slice failed: {r.stderr[-200:]}")
        return None

    out_path = OUT_DIR / f"template_{label}.mp4"
    # Warm horror grade + music at full volume (no narration to compete with)
    compose_cmd = [
        "ffmpeg", "-y",
        "-i", str(silent_slice),
        "-i", str(music_path),
        "-t", f"{take:.2f}",
        "-filter_complex",
        "[0:v]eq=saturation=0.92:contrast=1.08:brightness=-0.03[v];"
        "[1:a]volume=0.85,aloop=loop=-1:size=2e9[a]",
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-shortest",
        "-movflags", "+faststart",
        str(out_path),
    ]
    r = subprocess.run(compose_cmd, capture_output=True, text=True, timeout=180)
    silent_slice.unlink(missing_ok=True)
    if out_path.exists() and out_path.stat().st_size > 100_000:
        log(f"  ✓ {out_path.name} ({out_path.stat().st_size // 1024} KB)")
        return out_path
    log(f"  ✗ render failed: {r.stderr[-200:]}")
    return None


def send_to_telegram(mp4: Path, caption: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    with open(mp4, "rb") as f:
        files = {"video": f}
        data = {"chat_id": chat, "caption": caption, "supports_streaming": "true"}
        r = requests.post(f"https://api.telegram.org/bot{token}/sendVideo",
                          files=files, data=data, timeout=120)
    return r.ok and r.json().get("ok", False)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    clips = sorted(BROLL_DIR.glob("*.mp4"))
    if not clips:
        log(f"No clips found in {BROLL_DIR}")
        return
    log(f"Found {len(clips)} clips and {len(MUSIC_TRACKS)} music tracks")

    # Build 10 unique (clip, music) combos.
    # 7 use philmen_bg (primary) with different clips; 3 use other music for variety.
    random.shuffle(clips)
    combos = []
    for i in range(min(7, len(clips))):
        combos.append((clips[i], "philmen_bg"))
    for i, music in enumerate(MUSIC_TRACKS[1:4]):
        combos.append((clips[(7 + i) % len(clips)], music))
    combos = combos[:10]

    tmp_dir = Path("/tmp") / "horror_template_previews"
    tmp_dir.mkdir(exist_ok=True)
    sent = 0
    for idx, (clip, music) in enumerate(combos, 1):
        out = build_preview(idx, clip, music, tmp_dir)
        if out is None:
            continue
        caption = (
            f"🎬 Horror Template #{idx:02d}\n"
            f"Clip: {clip.name}\n"
            f"Music: {music}\n\n"
            f"React 👍 to approve, 👎 to skip"
        )
        if send_to_telegram(out, caption):
            sent += 1
            log(f"  ✓ sent #{idx} to Telegram")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    log(f"\n=== DONE — {sent}/{len(combos)} horror templates sent ===")


if __name__ == "__main__":
    main()
