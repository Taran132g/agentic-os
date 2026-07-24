#!/usr/bin/env python3
"""Render a movie-scene edit in the Growth-tapes format, but with the MOVIE
footage itself as the b-roll (instead of Minecraft). Reuses content_pipeline's
ytbox framing, grade+music mix, caption burn, and VHS overlay.

Usage:
  python3 render_movie_edit.py <video.mp4> <music_name> <credit> <yt_title> <out.mp4> <captions.json>

captions.json = list of {"text": str, "dur": float} segments (empty text = silent gap).
Static ambience is OFF; music enters after MUSIC_DELAY (the pause) and sits under
the scene's own audio.
"""
import json, sys, shutil, subprocess, tempfile
from pathlib import Path
import content_pipeline as cp

video, music_name, credit, yt_title, out_path, caps_json = sys.argv[1:7]
video = Path(video); out_path = Path(out_path)
cp.FRAME_MODE = "ytbox"                      # movie plays in the fake-YouTube box
# pause before the music bed enters; override with env MUSIC_DELAY (0 = start immediately)
import os as _os
MUSIC_DELAY = float(_os.environ.get("MUSIC_DELAY", "3.0"))

skip_caps = caps_json.lower() == "none"
if not skip_caps:
    segs = json.loads(Path(caps_json).read_text())
    durations = [s["dur"] for s in segs]
    segments  = [{"text": s["text"]} for s in segs]

with tempfile.TemporaryDirectory(prefix="movedit_") as tmp:
    tmp = Path(tmp)
    # 1) extract the scene's own audio (this is the "voice" track)
    scene_audio = tmp / "scene.m4a"
    subprocess.run([cp.FFMPEG, "-y", "-i", str(video), "-vn",
                    "-c:a", "aac", "-b:a", "192k", str(scene_audio)],
                   capture_output=True)

    # 2) ytbox-frame the movie footage + mux the scene audio
    seg = tmp / "seg.mp4"
    cp.assemble_segment(scene_audio, video, "", seg, tail=0.0)

    # 3) stage the popular music bed
    src = cp.MUSIC_CACHE / f"{music_name}.mp3"
    shutil.copyfile(src, cp.MUSIC_PATH)
    cp._strip_leading_silence(cp.MUSIC_PATH)

    # 4) grade + music with the pause, NO static
    graded = tmp / "graded.mp4"
    cp.apply_grade(seg, graded, music_delay=MUSIC_DELAY, ambience="none")

    # 5) VHS captions (karaoke of the dialogue) — skipped when caps arg == "none"
    if skip_caps:
        captioned = graded
    else:
        captioned = tmp / "cap.mp4"
        try:
            cp.burn_captions(graded, captioned, segments, durations, style="vhs")
        except Exception as e:
            print(f"  caption burn failed ({e}) — continuing without captions")
            captioned = graded

    # 6) VHS overlay + film credit (top-right red stamp) + fake-YT title
    vhs = tmp / "vhs.mp4"
    cp.apply_vhs_overlay(captioned, vhs, date_text=cp.VHS_DEFAULT_DATE,
                         label=credit, yt_title=yt_title)

    # 7) iOS-safe AAC remux
    subprocess.run([cp.FFMPEG, "-y", "-i", str(vhs), "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                    str(out_path)], capture_output=True)

print(f"Done → {out_path}  ({out_path.stat().st_size/1e6:.1f} MB, "
      f"{cp.get_duration(out_path):.1f}s)")
