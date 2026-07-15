#!/usr/bin/env python3
"""
clip_prep.py — CapCut prep kits: the pipeline does discovery + prep, Taran
does the final assembly by feel.

For a scored candidate, emits everything tedious about the edit, nothing
about the taste:

  ~/Desktop/ClipKits/<creator>_<video>_c<n>/
    base_bw.mp4      speech segment — sentence-complete, 9:16 crop, graded
                     (crushed B&W for motivation), clean audio, NO captions
    captions.srt     word-timed phrase subtitles (import into CapCut, or use
                     auto-captions and just match the style)
    broll/           2-3 vetted cutaways, letterbox-stripped, grade-matched
    music/           the phonk bed + an alternate
    EDIT_SHEET.txt   payoff timestamp, red keywords, suggested cut points,
                     post caption + hashtags, CapCut checklist

Usage:
    python3 clip_pipeline.py prep <creator> <video_id> <n>
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from clip_enhance import (
    MUSIC_DIR, TEMPLATES,
    _build_phrases, _clip_words, _crop_rect, _detect_layout, _energy,
    _src_dims, _work_file, fetch_broll, get_edl,
)
from clip_pipeline import MAX_CLIP_S, _load_creators, _s_to_ts, _video_dir, get_words

KITS_ROOT = Path.home() / "Desktop" / "ClipKits"

GENRE_BROLL = [
    "gym training dark cinematic",
    "man running alone rain night cinematic",
]


def _srt_ts(sec: float) -> str:
    ms = int(round(max(sec, 0) * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _write_srt(phrases: list[tuple], out: Path) -> None:
    blocks = []
    for i, (s, e, words) in enumerate(phrases, 1):
        text = " ".join(w for w, _ in words)
        blocks.append(f"{i}\n{_srt_ts(s)} --> {_srt_ts(e)}\n{text}\n")
    out.write_text("\n".join(blocks))


def prep(creator: str, video_id: str, idx: int) -> Path:
    vdir = _video_dir(creator, video_id)
    candidates = json.loads((vdir / "candidates.json").read_text())
    if not 1 <= idx <= len(candidates):
        raise SystemExit(f"candidate {idx} out of range (1-{len(candidates)})")
    c = candidates[idx - 1]
    creator_cfg = _load_creators().get(creator, {})
    is_motivation = creator_cfg.get("niche") == "motivation"

    t0 = max(c["start"] - 0.5, 0.0)
    end = c["end"]
    if is_motivation:   # never cut a speech mid-thought
        prev = end
        for w in get_words(vdir):
            if not end <= w["start"] <= end + 8:
                continue
            if w["start"] - prev >= 0.7:
                break
            prev = w["end"]
        end = prev + 0.4
    dur = min(end + 0.8, t0 + MAX_CLIP_S) - t0

    work = _work_file(vdir, idx, t0, dur)
    layout = _detect_layout(work, vdir, idx, dur, creator_cfg)
    if not layout.get("action") and layout.get("face"):
        layout = {**layout, "action": layout["face"]}
    cwords = _clip_words(get_words(vdir), t0, dur)
    spikes, lulls = _energy(work, dur)
    print(f"🧰 '{c['hook']}' {dur:.0f}s — spikes {spikes}")
    edl = get_edl(creator, c, cwords, dur, spikes, lulls, layout,
                  "hard_motivation" if is_motivation else None)
    tmpl = TEMPLATES.get(edl["template"], {})

    kit = KITS_ROOT / f"{creator}_{video_id}_c{idx}"
    (kit / "broll").mkdir(parents=True, exist_ok=True)
    (kit / "music").mkdir(exist_ok=True)

    # base clip: crop + grade, clean audio, NO captions
    W, H = _src_dims(work)
    w, h, x, y = _crop_rect(layout.get("action"), W, H, 1.0)
    bw = edl.get("payoff_grade") == "bw_full"
    grade = ("hue=s=0,eq=contrast=1.24:brightness=-0.05,"
             "vignette=PI/3.9,noise=alls=6:allf=t" if bw
             else "eq=contrast=1.03:saturation=1.08")
    base = kit / "base_bw.mp4"
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", str(work),
         "-vf", f"crop={w}:{h}:{x}:{y},scale=1080:1920,setsar=1,{grade}",
         "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(base)],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"base render failed:\n{r.stderr[-800:]}")
    print(f"  🎥 base_bw.mp4 ({dur:.0f}s)")

    # captions.srt — identity time map (base clip == candidate window)
    phrases = _build_phrases(cwords, lambda t: t, edl["emphasis"])
    _write_srt(phrases, kit / "captions.srt")
    print(f"  💬 captions.srt ({len(phrases)} phrase cues)")

    # b-roll: claude's payoff query + genre staples, grade-matched
    queries = ([edl["broll_query"]] if edl.get("broll_query") else []) \
        + (GENRE_BROLL if is_motivation else [])
    n_broll = 0
    for q in queries[:3]:
        p = fetch_broll(q)
        if not p:
            continue
        dest = kit / "broll" / f"{p.stem}.mp4"
        if bw:
            subprocess.run(["ffmpeg", "-y", "-i", str(p),
                            "-vf", "hue=s=0,eq=contrast=1.2:brightness=-0.03",
                            "-c:v", "libx264", "-preset", "veryfast",
                            "-crf", "21", "-an", str(dest)],
                           capture_output=True)
        else:
            shutil.copyfile(p, dest)
        if dest.exists():
            n_broll += 1
    print(f"  🎞 {n_broll} b-roll snippet(s)")

    for name in ("brazilian-phonk.mp3", "dark-piano.mp3"):
        src = MUSIC_DIR / name
        if src.exists():
            shutil.copyfile(src, kit / "music" / name)

    tags = creator_cfg.get("hashtags", f"#{creator} #motivation")
    red = ", ".join(sorted(w.upper() for w in edl["emphasis"])) or "—"
    cuts = ", ".join(f"{t:.1f}s" for t in spikes) or "—"
    sheet = f"""EDIT SHEET — {c['hook']}
{'=' * 60}
Clip: {creator}/{video_id} candidate {idx}
Source window: {_s_to_ts(t0)} → {_s_to_ts(t0 + dur)}  ({dur:.0f}s)
Why it was clipped: {c.get('reason', '—')}

PAYOFF: {edl['payoff_t']:.1f}s into base_bw.mp4
  → hardest moment. Land your best b-roll / zoom / music drop HERE.
SUGGESTED CUT POINTS (measured audio spikes): {cuts}
QUIET LULLS (let these breathe, or trim): {', '.join(f'{t:.1f}s' for t in lulls) or '—'}
RED KEYWORDS (pop these in the captions): {red}

POST CAPTION: {edl['caption']} {tags}

CAPCUT CHECKLIST
1. New project → import base_bw.mp4 + broll/ + music/
2. Captions: import captions.srt (or auto-captions) → your saved style,
   center-mid, ALL CAPS, red on the keywords above
3. Drag one b-roll clip onto the payoff timestamp; cut others on spikes
4. Music under speech (~-18dB), swell at the payoff
5. Slow push-in across the build if it feels flat — nothing else
6. Export 1080x1920 30fps → post with a TRENDING sound where it fits
"""
    (kit / "EDIT_SHEET.txt").write_text(sheet)
    try:
        from tools import clip_sheet
        clip_sheet.append(creator, c["hook"], kit.name,
                          notes=f"{dur:.0f}s · prep kit (CapCut)")
    except Exception as e:  # noqa: BLE001
        print(f"⚠ sheet tracking skipped: {e}")
    print(f"✅ kit → {kit}")
    return kit


def cmd_prep(args):
    prep(args[0], args[1], int(args[2]))


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    cmd_prep(sys.argv[1:])
