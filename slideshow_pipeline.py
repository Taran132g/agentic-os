#!/usr/bin/env python3
"""
slideshow_pipeline.py — TikTok photo-carousel generator.

Slideshows are the highest reach-per-effort format on TikTok and sidestep
video-editing craft entirely. Three niches:

  moments  <creator> <video_id> [n]   Streamer "moments that went too hard"
                                      carousel — best FRAME from each scored
                                      clip candidate, graded cinematic, quote
                                      caption, hook cover. Campaign-tied.
  tierlist                            "Ranking streamers by aura" — one slide
                                      per streamer with rank + verdict.
  quotes                              Stoic/motivation quote cards over
                                      cinematic b-roll frames (own-niche).

Output: ~/Desktop/Slideshows/<slug>/slide_NN.png + post.txt (caption, tags,
sound guidance). Add the trending sound IN the TikTok app when posting —
that's how photo posts get indexed.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from clip_enhance import _claude_json_obj, fetch_broll
from clip_pipeline import CACHE, _load_creators, _video_dir, get_words

BASE = Path(__file__).resolve().parent
OUT_ROOT = Path.home() / "Desktop" / "Slideshows"
W, H = 1080, 1920

FONT_HOOK = "/opt/homebrew/lib/python3.14/site-packages/captacity/assets/fonts/Bangers-Regular.ttf"
FONT_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
FONT_SERIF = "/System/Library/Fonts/Supplemental/Georgia Bold.ttf"


# ── image helpers ────────────────────────────────────────────────────────────

def _extract_frame(video: Path, t: float) -> Image.Image | None:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp = Path(f.name)
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}",
                        "-i", str(video), "-frames:v", "1", str(tmp)],
                       capture_output=True)
    if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        return None
    img = Image.open(tmp).convert("RGB")
    img.load()
    tmp.unlink(missing_ok=True)
    return img


def _sharpness(img: Image.Image) -> float:
    g = np.asarray(img.convert("L").resize((240, 135)), dtype=np.float32)
    return float(np.var(np.diff(g, axis=0)) + np.var(np.diff(g, axis=1)))


def best_frame(video: Path, t0: float, t1: float) -> Image.Image | None:
    """Sharpest of 5 sampled frames in the window (skips motion blur)."""
    cands = []
    for frac in (0.3, 0.45, 0.6, 0.75, 0.88):
        img = _extract_frame(video, t0 + (t1 - t0) * frac)
        if img:
            cands.append((_sharpness(img), img))
    return max(cands, key=lambda c: c[0])[1] if cands else None


def _crop_9_16(img: Image.Image, box: list | None = None) -> Image.Image:
    """Crop to 9:16 around `box` ([x,y,w,h] fractions) or center, → 1080x1920."""
    iw, ih = img.size
    if box:
        cx = (box[0] + box[2] / 2) * iw
        cy = (box[1] + box[3] / 2) * ih
        bh = box[3] * ih
    else:
        cx, cy, bh = iw / 2, ih / 2, ih
    h = min(max(bh * 1.15, iw * 16 / 9 * 0.5), ih)
    w = h * 9 / 16
    if w > iw:
        w, h = iw, iw * 16 / 9
    x = min(max(cx - w / 2, 0), iw - w)
    y = min(max(cy - h / 2, 0), ih - h)
    return img.crop((int(x), int(y), int(x + w), int(y + h))).resize(
        (W, H), Image.LANCZOS)


_VIGNETTE: np.ndarray | None = None


def _grade(img: Image.Image, dark: float = 0.0) -> Image.Image:
    """Cinematic pass: contrast/color/sharpen + vignette (+ optional darken)."""
    global _VIGNETTE
    img = ImageEnhance.Contrast(img).enhance(1.06)
    img = ImageEnhance.Color(img).enhance(1.12)
    img = ImageEnhance.Sharpness(img).enhance(1.35)
    if _VIGNETTE is None:
        yy, xx = np.mgrid[0:H, 0:W]
        d = np.sqrt(((xx - W / 2) / (W / 2)) ** 2
                    + ((yy - H / 2) / (H / 2)) ** 2)
        _VIGNETTE = np.clip(1.0 - 0.30 * np.clip(d - 0.45, 0, None) ** 2,
                            0.55, 1.0)[..., None]
    arr = np.asarray(img, dtype=np.float32) * _VIGNETTE * (1.0 - dark)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and draw.textlength(trial, font=font) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def _text_block(img: Image.Image, text: str, font, y_center: int,
                fill=(255, 255, 255), box: bool = True, max_w: int = 900,
                align_shadow: bool = True, line_gap: int = 14) -> None:
    """Centered wrapped text with TikTok-style rounded backing box."""
    draw = ImageDraw.Draw(img, "RGBA")
    lines = _wrap(draw, text, font, max_w)
    asc, desc = font.getmetrics()
    lh = asc + desc + line_gap
    total = lh * len(lines) - line_gap
    y = y_center - total // 2
    for line in lines:
        tw = draw.textlength(line, font=font)
        x = (W - tw) / 2
        if box:
            pad_x, pad_y = 26, 10
            draw.rounded_rectangle(
                (x - pad_x, y - pad_y, x + tw + pad_x, y + asc + desc + pad_y - 6),
                radius=16, fill=(0, 0, 0, 150))
        elif align_shadow:
            draw.text((x + 3, y + 3), line, font=font, fill=(0, 0, 0, 220))
        draw.text((x, y), line, font=font, fill=fill)
        y += lh


def _index_chip(img: Image.Image, k: int, n: int) -> None:
    draw = ImageDraw.Draw(img, "RGBA")
    f = _font(FONT_BOLD, 40)
    txt = f"{k}/{n}"
    tw = draw.textlength(txt, font=f)
    x, y = W - tw - 70, 90
    draw.rounded_rectangle((x - 20, y - 10, x + tw + 20, y + 52),
                           radius=14, fill=(0, 0, 0, 140))
    draw.text((x, y), txt, font=f, fill=(255, 255, 255))


def _watermark(img: Image.Image, creator: str) -> None:
    wm = _load_creators().get(creator, {}).get("watermark", "")
    p = BASE / wm if wm else None
    if not (p and p.exists()):
        return
    mark = Image.open(p).convert("RGBA")
    mw = 430
    mark = mark.resize((mw, int(mark.height * mw / mark.width)), Image.LANCZOS)
    img.paste(mark, ((W - mw) // 2, 200), mark)


def _save_set(slug: str, slides: list[Image.Image], post_text: str) -> Path:
    out = OUT_ROOT / slug
    out.mkdir(parents=True, exist_ok=True)
    for i, s in enumerate(slides, 1):
        s.save(out / f"slide_{i:02d}.png", optimize=True)
    (out / "post.txt").write_text(post_text)
    print(f"✅ {len(slides)} slides → {out}")
    return out


SOUND_NOTE = ("SOUND: add a TRENDING sound in the TikTok app when posting "
              "(search the niche + sort by 'this week') — photo posts get "
              "indexed by the sound. Do NOT post silent.")


def _portrait_frame(vdir: Path, creator: str) -> Image.Image | None:
    """Frame where the STREAMER is most prominent — claude vision picks from
    a numbered 3x3 sample grid. Cached per video."""
    src = vdir / "source.mp4"
    cache = vdir / "portrait.json"
    if cache.exists():
        return _extract_frame(src, json.loads(cache.read_text())["t"])
    pr = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                         "format=duration", "-of", "csv=p=0", str(src)],
                        capture_output=True, text=True)
    dur = float(pr.stdout.strip() or 0)
    ts = [dur * f for f in (0.06, 0.18, 0.3, 0.42, 0.54, 0.66, 0.78, 0.88, 0.96)]
    tiles, tw, th = [], 480, 270
    for i, t in enumerate(ts):
        f = _extract_frame(src, t)
        if not f:
            return None
        f = f.resize((tw, th))
        d = ImageDraw.Draw(f)
        d.rectangle((0, 0, 78, 62), fill=(0, 0, 0))
        d.text((16, 6), str(i + 1), font=_font(FONT_BOLD, 48),
               fill=(255, 255, 0))
        tiles.append(f)
    grid = Image.new("RGB", (tw * 3, th * 3))
    for i, f in enumerate(tiles):
        grid.paste(f, ((i % 3) * tw, (i // 3) * th))
    gp = vdir / "portrait_grid.png"
    grid.save(gp)
    raw = _claude_json_obj(
        f"Read the image {gp} — a 3x3 grid of numbered frames (1-9, yellow "
        f"number top-left of each tile) from a stream by '{creator}'. Which "
        "tile shows the STREAMER most clearly and prominently (their face "
        "visible, well lit, front of frame — not other people, not gameplay)? "
        'STRICT JSON only: {"pick": <1-9>}', tools="Read")
    try:
        pick = int(raw.get("pick", 5))
    except (TypeError, ValueError):
        pick = 5
    t = ts[min(max(pick - 1, 0), 8)]
    cache.write_text(json.dumps({"t": t}))
    return _extract_frame(src, t)


# ── music + video rendering (slideshow → mp4 with sound) ────────────────────

MUSIC_YT = BASE / "music_yt"


def fetch_music(query: str, need_s: float = 40.0) -> Path | None:
    """Download a music bed section from YouTube (cached by query slug)."""
    MUSIC_YT.mkdir(exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", query.lower()).strip("_")[:50]
    cached = MUSIC_YT / f"{slug}.mp3"
    if cached.exists():
        return cached
    r = subprocess.run(["yt-dlp", f"ytsearch5:{query}", "--flat-playlist",
                        "--print", "%(id)s|%(duration)s"],
                       capture_output=True, text=True, timeout=120)
    pick = None
    for line in r.stdout.strip().splitlines():
        vid, d = (line.split("|", 1) + [""])[:2]
        try:
            ds = float(d)
        except ValueError:
            continue
        if 60 <= ds <= 420:
            pick = (vid, ds)
            break
    if not pick:
        print(f"  ⚠ no music result for “{query}”")
        return None
    vid, ds = pick
    start = min(ds * 0.15, 30.0)
    tmp = MUSIC_YT / f"_{slug}.m4a"
    subprocess.run(["yt-dlp", f"https://www.youtube.com/watch?v={vid}",
                    "-f", "bestaudio",
                    "--download-sections", f"*{start:.0f}-{start + need_s + 6:.0f}",
                    "--force-overwrites", "-o", str(tmp)],
                   capture_output=True, text=True, timeout=240)
    if not tmp.exists():
        return None
    subprocess.run(["ffmpeg", "-y", "-i", str(tmp), "-t", f"{need_s + 4:.0f}",
                    "-ar", "44100", "-b:a", "192k", str(cached)],
                   capture_output=True)
    tmp.unlink(missing_ok=True)
    return cached if cached.exists() else None


def _xfade_chain(n: int, per: float, fade: float) -> tuple[str, float]:
    fc, total = "", per
    for k in range(1, n):
        src = "[v0]" if k == 1 else f"[x{k - 1}]"
        fc += (f"{src}[v{k}]xfade=transition=fade:duration={fade}:"
               f"offset={total - fade:.2f}[x{k}];")
        total += per - fade
    return fc, total


def _music_map(fc: str, m_idx: int, total: float) -> str:
    return fc + (f"[{m_idx}:a]atrim=0:{total:.2f},"
                 f"aformat=sample_rates=44100:channel_layouts=stereo,"
                 f"afade=t=in:d=0.6,afade=t=out:st={total - 1.4:.2f}:d=1.4,"
                 f"volume=0.9[aout]")


def slides_to_video(pngs: list[Path], music: Path | None, out: Path,
                    per: float = 3.2, fade: float = 0.45) -> None:
    """Stills → motion slideshow: slow Ken Burns push per slide + crossfades
    + music bed."""
    cmd = ["ffmpeg", "-y"]
    for p in pngs:
        cmd += ["-loop", "1", "-framerate", "30", "-t", f"{per + 0.1:.2f}",
                "-i", str(p)]
    if music:
        cmd += ["-i", str(music)]
    fc = ""
    for i in range(len(pngs)):
        fc += (f"[{i}:v]zoompan=z='min(1.0+0.0011*on,1.13)':"
               f"x='(iw-iw/zoom)/2':y='(ih-ih/zoom)*0.45':d=1:"
               f"s=1080x1920:fps=30,trim=0:{per:.2f},setpts=PTS-STARTPTS,"
               f"format=yuv420p[v{i}];")
    chain, total = _xfade_chain(len(pngs), per, fade)
    fc += chain
    vout = f"[x{len(pngs) - 1}]" if len(pngs) > 1 else "[v0]"
    cmd += ["-t", f"{total:.2f}"]
    if music:
        fc = _music_map(fc, len(pngs), total)
        maps = ["-map", vout, "-map", "[aout]"]
    else:
        maps = ["-map", vout]
    cmd += ["-filter_complex", fc.rstrip(";"), *maps,
            "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"video render failed:\n{r.stderr[-1200:]}")
    print(f"🎬 {out.name} ({total:.0f}s)")


def segments_to_video(segs: list[tuple[Path, Path]], music: Path | None,
                      out: Path, per: float = 4.2, fade: float = 0.5) -> None:
    """(bg_video, text_overlay_png) pairs → motion quote video + music."""
    cmd = ["ffmpeg", "-y"]
    for v, _ in segs:
        cmd += ["-i", str(v)]
    for _, t in segs:
        cmd += ["-loop", "1", "-framerate", "30", "-t", f"{per + 0.1:.2f}",
                "-i", str(t)]
    if music:
        cmd += ["-i", str(music)]
    n = len(segs)
    fc = ""
    for i in range(n):
        fc += (f"[{i}:v]trim=0:{per:.2f},setpts=PTS-STARTPTS,"
               f"scale=1080:1920:force_original_aspect_ratio=increase,"
               f"crop=1080:1920,eq=brightness=-0.1:saturation=1.08,"
               f"vignette=PI/4.4,fps=30[b{i}];"
               f"[b{i}][{n + i}:v]overlay,format=yuv420p,"
               f"trim=0:{per:.2f},setpts=PTS-STARTPTS[v{i}];")
    chain, total = _xfade_chain(n, per, fade)
    fc += chain
    vout = f"[x{n - 1}]" if n > 1 else "[v0]"
    cmd += ["-t", f"{total:.2f}"]
    if music:
        fc = _music_map(fc, 2 * n, total)
        maps = ["-map", vout, "-map", "[aout]"]
    else:
        maps = ["-map", vout]
    cmd += ["-filter_complex", fc.rstrip(";"), *maps,
            "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"video render failed:\n{r.stderr[-1200:]}")
    print(f"🎬 {out.name} ({total:.0f}s)")


# ── niche 1: streamer moments carousel (campaign-tied) ──────────────────────

def moments(creator: str, video_id: str, n: int = 6) -> Path:
    vdir = _video_dir(creator, video_id)
    src = vdir / "source.mp4"
    cands = json.loads((vdir / "candidates.json").read_text())
    layout = {}
    if (vdir / "layout.json").exists():
        layout = json.loads((vdir / "layout.json").read_text())
    box = layout.get("face") or layout.get("action")

    picked, used = [], []
    for c in sorted(cands, key=lambda c: -c["score"]):
        if any(abs(c["start"] - u) < 20 for u in used):
            continue
        picked.append(c)
        used.append(c["start"])
        if len(picked) >= n:
            break

    slides = []
    cap_f = _font(FONT_BOLD, 54)
    frames = []
    for c in picked:
        img = best_frame(src, c["start"] + 0.5, c["end"])
        if img:
            frames.append((c, _grade(_crop_9_16(img, box))))
    if not frames:
        raise SystemExit("no usable frames")

    # cover: darkened portrait of the streamer + big hook
    name = creator.upper().replace("PLAQUEBOYMAX", "PBM")
    pimg = _portrait_frame(vdir, creator)
    cover_base = _grade(_crop_9_16(pimg, box)) if pimg else frames[0][1]
    cover = _grade(cover_base.copy(), dark=0.45)
    _text_block(cover, f"{name} MOMENTS", _font(FONT_HOOK, 128), 760,
                box=False)
    _text_block(cover, "THAT WENT TOO HARD", _font(FONT_HOOK, 128), 900,
                fill=(245, 176, 65), box=False)
    _text_block(cover, "wait for the last one →", _font(FONT_BOLD, 46), 1120,
                box=True)
    slides.append(cover)

    for k, (c, img) in enumerate(frames, 1):
        s = img.copy()
        _watermark(s, creator)
        _text_block(s, f"“{c['hook']}”", cap_f, 1560)
        _index_chip(s, k, len(frames))
        slides.append(s)

    cfg = _load_creators().get(creator, {})
    tags = cfg.get("hashtags", f"#{creator} #fyp")
    post = (f"CAPTION: {name} moments that went too hard 💀 {tags}\n"
            f"CAMPAIGN: {cfg.get('campaign', '—')}\n{SOUND_NOTE}\n"
            "Slides are ordered — the cover promises the last one, keep order.")
    return _save_set(f"{creator}_{video_id}_moments", slides, post)


# ── niche 4: aura tier list ──────────────────────────────────────────────────

TIER = [  # (creator, video_id, rank, aura, verdict)
    ("stableronaldo", "I3OPpXsWAw8", 3, 7.4,
     "Loses aura every time chat clips him. Recovers instantly on wins."),
    ("jasontheween", "v2813966129", 2, 8.9,
     "Dean of Streamer University. Unbothered in every single clip."),
    ("plaqueboymax", "v2812970464", 1, 9.6,
     "Called his management mid-argument and WON. Untouchable."),
]


def tierlist() -> Path:
    slides = []
    frames = {}
    for creator, vid, rank, aura, verdict in TIER:
        vdir = _video_dir(creator, vid)
        layout = {}
        if (vdir / "layout.json").exists():
            layout = json.loads((vdir / "layout.json").read_text())
        img = _portrait_frame(vdir, creator)
        if img is None:
            cands = json.loads((vdir / "candidates.json").read_text())
            c = max(cands, key=lambda c: c["score"])
            img = best_frame(vdir / "source.mp4", c["start"] + 0.5, c["end"])
        frames[creator] = _grade(_crop_9_16(
            img, layout.get("face") or layout.get("action")))

    cover = _grade(frames["plaqueboymax"].copy(), dark=0.55)
    _text_block(cover, "RANKING STREAMERS", _font(FONT_HOOK, 118), 740, box=False)
    _text_block(cover, "BY AURA", _font(FONT_HOOK, 160), 900,
                fill=(245, 176, 65), box=False)
    _text_block(cover, "brutally honest edition", _font(FONT_BOLD, 46), 1110,
                box=True)
    slides.append(cover)

    for creator, vid, rank, aura, verdict in TIER:
        s = frames[creator].copy()
        name = creator.upper().replace("PLAQUEBOYMAX", "PBM") \
                             .replace("STABLERONALDO", "RON") \
                             .replace("JASONTHEWEEN", "JASON")
        _text_block(s, f"#{rank}", _font(FONT_HOOK, 200), 330,
                    fill=(245, 176, 65), box=False)
        _text_block(s, name, _font(FONT_HOOK, 110), 480, box=False)
        _text_block(s, f"aura: {aura}/10", _font(FONT_BOLD, 56), 1440)
        _text_block(s, verdict, _font(FONT_BOLD, 46), 1580)
        slides.append(s)

    outro = _grade(frames["jasontheween"].copy(), dark=0.6)
    _text_block(outro, "DISAGREE?", _font(FONT_HOOK, 150), 800, box=False)
    _text_block(outro, "comment your ranking ⬇", _font(FONT_BOLD, 54), 980)
    _text_block(outro, "follow for part 2", _font(FONT_BOLD, 46), 1090)
    slides.append(outro)

    post = ("CAPTION: ranking streamers by aura, be honest 💀 #pbm #jasontheween "
            "#stableronaldo #streamer #fyp\n"
            "NICHE: rage-bait ranking — the comments ARE the distribution.\n"
            "VIDEO: aura_tierlist_01.mp4 has the sound baked in — post that, "
            "or post the PNGs as a photo carousel with a trending sound.\n"
            + SOUND_NOTE)
    out = _save_set("aura_tierlist_01", slides, post)
    print("🎵 fetching aura sound…")
    music = fetch_music("aura phonk hard no copyright music", len(slides) * 3.2)
    slides_to_video(sorted(out.glob("slide_*.png")), music,
                    out / "aura_tierlist_01.mp4")
    return out


# ── niche 5: stoic quote cards over cinematic b-roll ─────────────────────────

QUOTES = [
    "Discipline is choosing between what you want now and what you want most.",
    "You will never always be motivated. Learn to be disciplined.",
    "Work in silence. Let success make the noise.",
    "Nobody is coming to save you. Get up.",
    "Your future self is watching you right now.",
    "Comfort is the slowest form of quitting.",
]


PLACES = [  # beautiful, deliberately non-American backdrops
    "santorini greece drone 4k cinematic",
    "swiss alps drone 4k cinematic",
    "kyoto japan temple 4k cinematic",
    "norway fjords drone 4k",
    "bali rice terraces drone 4k",
    "iceland waterfall drone 4k",
]


def quotes() -> Path:
    print("🌍 fetching world-places b-roll (cached after first run)…")
    bgs = []
    for q in PLACES:
        p = fetch_broll(q)
        if p:
            bgs.append(p)
    if len(bgs) < 3:
        raise SystemExit("not enough b-roll fetched — check network/yt-dlp")

    out_dir = OUT_ROOT / "world_quotes_01"
    out_dir.mkdir(parents=True, exist_ok=True)
    slides, seg_pairs = [], []
    qf = _font(FONT_SERIF, 68)
    for i, q in enumerate(QUOTES):
        bg = bgs[i % len(bgs)]
        img = _extract_frame(bg, 2.5) or _extract_frame(bg, 0.5)
        lum = float(np.asarray(img.convert("L")).mean()) / 255
        s = _grade(_crop_9_16(img), dark=min(max(0.15, lum * 1.1 - 0.05), 0.5))
        if i == 0:
            _text_block(s, "quotes that rewired my brain",
                        _font(FONT_BOLD, 52), 520)
        _text_block(s, f"“{q}”", qf, 960, box=False, max_w=860)
        _index_chip(s, i + 1, len(QUOTES))
        slides.append(s)
        # transparent overlay with the same text — laid over the MOVING b-roll
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        if i == 0:
            _text_block(ov, "quotes that rewired my brain",
                        _font(FONT_BOLD, 52), 520)
        _text_block(ov, f"“{q}”", qf, 960, box=False, max_w=860)
        _index_chip(ov, i + 1, len(QUOTES))
        ov_p = out_dir / f"_txt_{i:02d}.png"
        ov.save(ov_p)
        seg_pairs.append((bg, ov_p))

    post = ("CAPTION: save this. #discipline #mindset #stoic #selfimprovement\n"
            "NICHE: own-page motivation — world-places edition (no US spots).\n"
            "VIDEO: world_quotes_01.mp4 has motion b-roll + music baked in — "
            "post that, or post PNGs as a carousel with a trending sound.\n"
            + SOUND_NOTE)
    _save_set("world_quotes_01", slides, post)
    print("🎵 fetching motivational music…")
    music = fetch_music("motivational cinematic piano epic background music "
                        "no copyright", len(QUOTES) * 4.2)
    segments_to_video(seg_pairs, music, out_dir / "world_quotes_01.mp4")
    return out_dir


# ── entry ────────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    cmd = argv[0]
    if cmd == "moments":
        moments(argv[1], argv[2], int(argv[3]) if len(argv) > 3 else 6)
    elif cmd == "tierlist":
        tierlist()
    elif cmd == "quotes":
        quotes()
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
