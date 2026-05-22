#!/usr/bin/env python3
"""
Birthday video maker: all family photos (slideshow w/ crossfade) + video clips at the end.
Output: ~/Desktop/birthday_papa.mp4  (portrait 1080x1920)
"""
import subprocess
import sys
from pathlib import Path

FFMPEG = "/opt/homebrew/bin/ffmpeg"
DOWNLOADS = Path.home() / "Downloads"
OUTPUT = Path.home() / "Desktop" / "birthday_papa.mp4"

SLIDE_DUR = 4.0   # seconds per photo
FADE_DUR  = 1.0   # crossfade transition seconds
OUT_W, OUT_H = 1080, 1920  # portrait 9:16
MUSIC = Path("/tmp/morni_diljit.mp3")   # Morni — Diljit Dosanjh

# ── Collect media ──────────────────────────────────────────────────────────────
photos = sorted(DOWNLOADS.glob("*.JPG")) + sorted(DOWNLOADS.glob("*.jpg"))
videos = sorted(DOWNLOADS.glob("*.MP4")) + sorted(DOWNLOADS.glob("*.mp4"))

n = len(photos)
print(f"Photos: {n}  |  Video clips: {len(videos)}")

# ── Step 1: Build photo slideshow segment ──────────────────────────────────────
# Each photo loaded as looped still image for SLIDE_DUR + FADE_DUR seconds
# xfade chain connects them with crossfades

scale_pad = (
    f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease,"
    f"pad={OUT_W}:{OUT_H}:(ow-iw)/2:(oh-ih)/2:color=black,"
    f"setsar=1,fps=30"
)

cmd_photos = [FFMPEG, "-y"]
for p in photos:
    cmd_photos += ["-loop", "1", "-t", str(SLIDE_DUR + FADE_DUR), "-i", str(p)]

filter_parts = []
for i in range(n):
    filter_parts.append(f"[{i}:v]{scale_pad}[s{i}]")

prev = "s0"
for i in range(1, n):
    offset = i * SLIDE_DUR - FADE_DUR
    out_label = f"xf{i}" if i < n - 1 else "slideout"
    filter_parts.append(
        f"[{prev}][s{i}]xfade=transition=fade:duration={FADE_DUR}:offset={offset:.1f}[{out_label}]"
    )
    prev = f"xf{i}"

filter_parts.append(f"[slideout]format=yuv420p[vslide]")
filter_complex = ";".join(filter_parts)

SLIDE_TMP = Path("/tmp/birthday_slide.mp4")
cmd_photos += [
    "-filter_complex", filter_complex,
    "-map", "[vslide]",
    "-an",                    # no audio for photo segment
    "-c:v", "libx264", "-crf", "20", "-r", "30",
    "-pix_fmt", "yuv420p",
    str(SLIDE_TMP)
]

print("Building photo slideshow…")
r = subprocess.run(cmd_photos, capture_output=True, text=True)
if r.returncode != 0:
    print("ERROR:", r.stderr[-3000:])
    sys.exit(1)
print(f"  Slide segment: {SLIDE_TMP}")

# ── Step 2: Scale video clips to 1080x1920 ────────────────────────────────────
scaled_clips = []
for i, v in enumerate(videos):
    out = Path(f"/tmp/birthday_clip_{i}.mp4")
    cmd_v = [
        FFMPEG, "-y", "-i", str(v),
        "-vf", f"{scale_pad}",
        "-c:v", "libx264", "-crf", "20", "-r", "30",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        str(out)
    ]
    print(f"Scaling video clip {i+1}/{len(videos)}…")
    r = subprocess.run(cmd_v, capture_output=True, text=True)
    if r.returncode != 0:
        print("ERROR:", r.stderr[-2000:])
        sys.exit(1)
    scaled_clips.append(out)

# ── Step 3: Concatenate slide + video clips ───────────────────────────────────
concat_list = Path("/tmp/birthday_concat.txt")
with open(concat_list, "w") as f:
    f.write(f"file '{SLIDE_TMP}'\n")
    for c in scaled_clips:
        f.write(f"file '{c}'\n")

print("Concatenating all segments…")
cmd_concat = [
    FFMPEG, "-y",
    "-f", "concat", "-safe", "0", "-i", str(concat_list),
    "-c:v", "libx264", "-crf", "20",
    "-an",                          # strip audio — music added in next step
    "-pix_fmt", "yuv420p",
    str(OUTPUT)
]
r = subprocess.run(cmd_concat, capture_output=True, text=True)
if r.returncode != 0:
    print("ERROR:", r.stderr[-2000:])
    sys.exit(1)

# ── Step 4: Overlay Morni as background music ─────────────────────────────────
TMP_SILENT = Path("/tmp/birthday_nomusic.mp4")
OUTPUT.rename(TMP_SILENT)

probe = subprocess.run(
    [FFMPEG.replace("ffmpeg", "ffprobe"), "-v", "quiet",
     "-show_entries", "format=duration",
     "-of", "default=noprint_wrappers=1:nokey=1", str(TMP_SILENT)],
    capture_output=True, text=True
)
total_dur = float(probe.stdout.strip())
fade_start = max(total_dur - 3.0, 0.0)

cmd_music = [
    FFMPEG, "-y",
    "-i", str(TMP_SILENT),
    "-stream_loop", "-1", "-i", str(MUSIC),
    "-filter_complex",
        f"[1:a]volume=0.85,afade=t=out:st={fade_start:.1f}:d=3[aud]",
    "-map", "0:v",
    "-map", "[aud]",
    "-shortest",
    "-c:v", "copy",
    "-c:a", "aac", "-b:a", "192k",
    str(OUTPUT)
]
print("Adding background music (Morni — Diljit Dosanjh)…")
r = subprocess.run(cmd_music, capture_output=True, text=True)
if r.returncode != 0:
    print("ERROR:", r.stderr[-2000:])
    sys.exit(1)

size_mb = OUTPUT.stat().st_size / 1_000_000
print(f"\nDone!  {OUTPUT}  ({size_mb:.1f} MB)")
