#!/usr/bin/env python3
"""
clip_pipeline.py — real-content clipping pipeline (creator clip programs / Whop).

Clips long-form creator content (YouTube VODs, Kick VODs) into 9:16 captioned
shorts. Unlike the TTS niches, the source is real footage — the pipeline
downloads, transcribes (YouTube auto-subs first, Whisper fallback), scores
moments with claude, cuts blurred-background vertical clips, and burns
word-by-word captions via captacity (same style as content_pipeline.py).

Usage:
    python3 clip_pipeline.py fetch <url> <creator>
    python3 clip_pipeline.py find <creator> [video_id]      # score moments
    python3 clip_pipeline.py cut <creator> <video_id> <n>   # render candidate n
    python3 clip_pipeline.py enhance <creator> <video_id> <n> [meme|aura] [broll=<file>]
    python3 clip_pipeline.py auto <url> <creator> [n_clips] # fetch+find+cut
    python3 clip_pipeline.py list <creator>

Output: ~/Desktop/Clips/<creator>/<video_id>_c<n>.mp4
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
BASE = Path(__file__).resolve().parent
CACHE = BASE / "clip_cache"
OUT_ROOT = HOME / "Desktop" / "Clips"

CLAUDE_MODEL = "sonnet"          # keep scoring off the Opus quota
CLAUDE_TIMEOUT = 180
MIN_CLIP_S = 15
MAX_CLIP_S = 55
CHUNK_CHARS = 9000               # transcript chars per claude scoring call
CANDIDATES_PER_CHUNK = 4

SCORE_PROMPT = """You are a short-form clipping editor working a creator clip program \
(TikTok / Reels / Shorts). The creator is "{creator}" — streaming/gym-world content: \
gym lifts and PRs, wild casino wins/losses, outbursts, jokes, hot takes, IRL chaos.

Below is a timestamped transcript chunk of a long-form video titled "{title}".
Pick the {n} strongest clip-worthy moments. A great clip: starts mid-action with a \
hook in the first 2 seconds, is self-contained, {min_s}-{max_s} seconds, and ends on \
a punchline/reaction/resolution. Prefer emotional spikes: yelling, laughing, disbelief, \
big money, big lifts, conflict, quotable one-liners. Avoid: sponsor reads, dead air, \
inside references that need context.

Respond with STRICT JSON only — an array (no markdown fences, no commentary):
[{{"start": "mm:ss", "end": "mm:ss", "hook": "overlay title, max 8 words, no emojis",
   "reason": "why it clips", "score": 1-10}}]
If nothing is clip-worthy, respond [].

TRANSCRIPT:
{transcript}"""

# creators.json "niche": "motivation" switches scoring to this prompt
MOTIVATION_PROMPT = """You are clipping motivational speeches into shorts for a \
men's hard-motivation page (David Goggins / Mike Mentzer edit style — B&W, music, \
adrenaline). Below is a timestamped transcript of "{title}" by {creator}.

Pick the {n} HARDEST self-contained passages: direct commands, brutal truths about \
pain/discipline/weakness, callus-the-mind lines, quotable one-liners that hit like a \
punch. A great cut: starts ON a strong sentence (no wind-up), builds, ends on the \
hardest line. {min_s}-{max_s} seconds. Avoid: thank-yous, audience references, names, \
setup context, jokes.

Respond with STRICT JSON only — an array (no markdown fences, no commentary):
[{{"start": "mm:ss", "end": "mm:ss", "hook": "overlay title, max 8 words, no emojis",
   "reason": "why it hits", "score": 1-10}}]

TRANSCRIPT:
{transcript}"""


# ── helpers ──────────────────────────────────────────────────────────────────

def _ts_to_s(ts: str) -> float:
    """'hh:mm:ss.mmm' | 'mm:ss' → seconds."""
    parts = ts.strip().split(":")
    parts = [p.replace(",", ".") for p in parts]
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    if len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    return float(parts[0])


def _s_to_ts(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _video_dir(creator: str, video_id: str) -> Path:
    return CACHE / creator / video_id


def _latest_video_id(creator: str) -> str:
    vids = sorted((CACHE / creator).glob("*/source.mp4"), key=lambda p: p.stat().st_mtime)
    if not vids:
        raise SystemExit(f"No fetched videos for '{creator}'. Run fetch first.")
    return vids[-1].parent.name


def _load_info(vdir: Path) -> dict:
    for p in vdir.glob("*.info.json"):
        return json.loads(p.read_text())
    return {}


# ── fetch ────────────────────────────────────────────────────────────────────

def fetch(url: str, creator: str, section: str | None = None) -> Path:
    """Download source video + auto-subs into clip_cache/<creator>/<id>/.

    section="00:00:00-01:00:00" bounds the download — used for multi-hour
    Twitch VODs so we transcribe/scan a window instead of an 8h stream."""
    dest = CACHE / creator
    dest.mkdir(parents=True, exist_ok=True)
    cmd = [
        "yt-dlp", url,
        "-o", str(dest / "%(id)s" / "source.%(ext)s"),
        "-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b",
        "--merge-output-format", "mp4",
        "--write-info-json",
        "--write-auto-subs", "--write-subs",
        # exact langs only — "en.*" matches ~50 auto-translated variants on big
        # channels (en-ar, en-bn, …) and YouTube 429s the burst
        "--sub-langs", "en,en-orig,en-US",
        "--convert-subs", "vtt",
        "--no-playlist",
    ]
    if section:
        cmd += ["--download-sections", f"*{section}"]
    print(f"⬇ Fetching {url}{' [' + section + ']' if section else ''} …")
    r = subprocess.run(cmd)
    if r.returncode != 0:
        # YouTube bot-check (429 aftermath) — retry authenticated via Chrome cookies
        print("  ↻ retrying with browser cookies (YouTube bot-check)…")
        subprocess.run(cmd + ["--cookies-from-browser", "chrome"], check=True)
    # newest dir wins
    vid = _latest_video_id(creator)
    print(f"✔ Cached as {creator}/{vid}")
    return _video_dir(creator, vid)


# ── transcript ───────────────────────────────────────────────────────────────

_CUE_RE = re.compile(r"(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})")
_WORD_RE = re.compile(r"<(\d{2}:\d{2}:\d{2}[.,]\d{3})><c>([^<]*)</c>")
_NOISE_RE = re.compile(r"\[(music|applause|laughter)\]", re.I)


def parse_vtt_words(vtt_path: Path) -> list[dict]:
    """YouTube auto-sub VTT → [{word, start, end}] with real word timings.

    Auto-VTT is 'rolling': each cue repeats the previous line untagged and adds
    new words with <t><c> tags. Only tagged lines carry new words, plus the
    leading untagged word of a tagged line (starts at cue start).
    """
    words: list[dict] = []
    cue_start = cue_end = None
    has_tags = "<c>" in vtt_path.read_text(errors="ignore")

    for raw in vtt_path.read_text(errors="ignore").splitlines():
        m = _CUE_RE.search(raw)
        if m:
            cue_start, cue_end = _ts_to_s(m.group(1)), _ts_to_s(m.group(2))
            continue
        if cue_start is None or not raw.strip():
            continue
        line = raw.strip()
        if has_tags:
            if "<c>" not in line:
                continue  # rolling repeat line
            lead = line.split("<", 1)[0].strip()
            tagged = _WORD_RE.findall(line)
            if lead and not _NOISE_RE.search(lead):
                nxt = _ts_to_s(tagged[0][0]) if tagged else cue_end
                words.append({"word": lead, "start": cue_start, "end": nxt})
            for i, (ts, w) in enumerate(tagged):
                w = w.strip()
                if not w or _NOISE_RE.search(w):
                    continue
                start = _ts_to_s(ts)
                end = _ts_to_s(tagged[i + 1][0]) if i + 1 < len(tagged) else cue_end
                words.append({"word": w, "start": start, "end": end})
        else:
            # plain cues (uploaded subs) — distribute words evenly
            text = _NOISE_RE.sub("", re.sub(r"<[^>]+>", "", line)).strip()
            toks = text.split()
            if not toks:
                continue
            dur = max(cue_end - cue_start, 0.2)
            step = dur / len(toks)
            for i, w in enumerate(toks):
                words.append({"word": w,
                              "start": cue_start + i * step,
                              "end": cue_start + (i + 1) * step})
    # de-dup identical (word,start) pairs that some VTTs emit
    seen, out = set(), []
    for w in words:
        key = (w["word"], round(w["start"], 2))
        if key not in seen:
            seen.add(key)
            out.append(w)
    return out


def transcribe_whisper(vdir: Path) -> list[dict]:
    """Fallback for sources without subs (Kick VODs). Needs faster-whisper."""
    try:
        from faster_whisper import WhisperModel
    except ModuleNotFoundError:
        # RuntimeError (not SystemExit) so daily's per-video guard can skip it
        raise RuntimeError(
            "No subtitles on this source and faster-whisper isn't installed "
            "(python3 -m pip install --break-system-packages faster-whisper)"
        )
    print("🎙 Whisper transcription (base.en, word timestamps)…")
    model = WhisperModel("base.en", compute_type="int8")
    segments, _ = model.transcribe(str(vdir / "source.mp4"), word_timestamps=True)
    words = []
    for seg in segments:
        for w in seg.words or []:
            words.append({"word": w.word.strip(), "start": w.start, "end": w.end})
    return words


def get_words(vdir: Path) -> list[dict]:
    cache = vdir / "words.json"
    if cache.exists():
        return json.loads(cache.read_text())
    vtts = sorted(vdir.glob("*.vtt"))
    words = parse_vtt_words(vtts[0]) if vtts else transcribe_whisper(vdir)
    if not words:
        words = transcribe_whisper(vdir)
    cache.write_text(json.dumps(words))
    return words


def words_to_transcript(words: list[dict], block_s: float = 12.0) -> str:
    """Words → '[mm:ss] text' blocks for the scoring prompt."""
    lines, buf, block_start = [], [], None
    for w in words:
        if block_start is None:
            block_start = w["start"]
        buf.append(w["word"])
        if w["start"] - block_start >= block_s:
            lines.append(f"[{_s_to_ts(block_start)}] {' '.join(buf)}")
            buf, block_start = [], None
    if buf:
        lines.append(f"[{_s_to_ts(block_start)}] {' '.join(buf)}")
    return "\n".join(lines)


# ── find (claude scoring) ────────────────────────────────────────────────────

def _claude_json(prompt: str) -> list[dict]:
    r = subprocess.run(
        ["claude", "-p", "--model", CLAUDE_MODEL, prompt],
        capture_output=True, text=True, timeout=CLAUDE_TIMEOUT,
    )
    if r.returncode != 0:
        print(f"⚠ claude failed: {r.stderr.strip()[:200]}")
        return []
    txt = r.stdout.strip()
    m = re.search(r"\[.*\]", txt, re.S)
    if not m:
        return []
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return []


def find(creator: str, video_id: str | None = None) -> list[dict]:
    video_id = video_id or _latest_video_id(creator)
    vdir = _video_dir(creator, video_id)
    info = _load_info(vdir)
    title = info.get("title", video_id)
    words = get_words(vdir)
    transcript = words_to_transcript(words)
    print(f"🔎 Scoring '{title}' — {len(words)} words, {len(transcript)} chars")

    # chunk long transcripts, keep global top candidates
    chunks = [transcript[i:i + CHUNK_CHARS] for i in range(0, len(transcript), CHUNK_CHARS)]
    tpl = MOTIVATION_PROMPT if _load_creators().get(creator, {}).get(
        "niche") == "motivation" else SCORE_PROMPT
    candidates: list[dict] = []
    for i, chunk in enumerate(chunks, 1):
        print(f"  → claude chunk {i}/{len(chunks)}…")
        prompt = tpl.format(
            creator=creator, title=title, n=CANDIDATES_PER_CHUNK,
            min_s=MIN_CLIP_S, max_s=MAX_CLIP_S, transcript=chunk,
        )
        for c in _claude_json(prompt):
            try:
                start, end = _ts_to_s(str(c["start"])), _ts_to_s(str(c["end"]))
            except (KeyError, ValueError):
                continue
            if end - start < 8:
                continue
            end = min(end, start + MAX_CLIP_S)
            candidates.append({
                "start": start, "end": end,
                "hook": str(c.get("hook", ""))[:80],
                "reason": str(c.get("reason", ""))[:200],
                "score": float(c.get("score", 5)),
            })
    candidates.sort(key=lambda c: -c["score"])
    (vdir / "candidates.json").write_text(json.dumps(candidates, indent=2))
    print(f"\n📋 {len(candidates)} candidates — {creator}/{video_id}")
    for i, c in enumerate(candidates, 1):
        print(f"  {i}. [{_s_to_ts(c['start'])}-{_s_to_ts(c['end'])}] "
              f"({c['score']:.0f}/10) {c['hook']} — {c['reason'][:80]}")
    return candidates


# ── cut (render one candidate) ───────────────────────────────────────────────

FONTS_DIR = "/opt/homebrew/lib/python3.14/site-packages/captacity/assets/fonts"
CAPTION_MARGIN_V = 430  # px from bottom @1920 — lower third, clear of mid-frame


def _ffmpeg_with_libass() -> str:
    """Brew ffmpeg here is a slim build (no subtitles filter); imageio-ffmpeg's
    static binary has libass, so prefer it for the caption burn."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ModuleNotFoundError:
        return "ffmpeg"


def _ass_ts(sec: float) -> str:
    cs = int(round(max(sec, 0) * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _write_ass(words: list[dict], t0: float, dur: float, out: Path) -> bool:
    """Word-highlight captions as ASS subs — lower third, not mid-frame.

    One Dialogue event per word: the whole 3-4 word line is drawn with the
    current word in amber (#F5B041 → ASS BGR &H41B0F5&).
    """
    import html
    clip_words = [
        {"word": re.sub(r"[{}\\]|>>+", "", html.unescape(w["word"])),
         "start": max(w["start"] - t0, 0.0),
         "end": min(w["end"] - t0, dur)}
        for w in words if t0 <= w["start"] < t0 + dur
    ]
    clip_words = [w for w in clip_words if w["word"].strip()]
    if not clip_words:
        return False
    # lines of ≤4 words, break on >1.2s gaps
    lines, cur = [], [clip_words[0]]
    for w in clip_words[1:]:
        if len(cur) >= 4 or w["start"] - cur[-1]["end"] > 1.2:
            lines.append(cur)
            cur = [w]
        else:
            cur.append(w)
    lines.append(cur)

    events = []
    for line in lines:
        for i, w in enumerate(line):
            end = line[i + 1]["start"] if i + 1 < len(line) else w["end"]
            text = " ".join(
                (r"{\c&H41B0F5&}" + x["word"] + r"{\c&HFFFFFF&}") if j == i else x["word"]
                for j, x in enumerate(line)
            )
            events.append(f"Dialogue: 0,{_ass_ts(w['start'])},{_ass_ts(end)},Cap,,0,0,0,,{text}")

    out.write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Cap,Bangers,88,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
        f"0,0,0,0,100,100,0,0,1,7,2,2,60,60,{CAPTION_MARGIN_V},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        + "\n".join(events) + "\n"
    )
    return True


def cut(creator: str, video_id: str, idx: int) -> Path:
    vdir = _video_dir(creator, video_id)
    candidates = json.loads((vdir / "candidates.json").read_text())
    if not 1 <= idx <= len(candidates):
        raise SystemExit(f"candidate {idx} out of range (1-{len(candidates)})")
    c = candidates[idx - 1]
    t0 = max(c["start"] - 0.5, 0.0)
    dur = min(c["end"] + 0.8, t0 + MAX_CLIP_S) - t0

    out_dir = OUT_ROOT / creator
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / f"{video_id}_c{idx}.mp4"

    ass_path = vdir / f"clip_{idx}.ass"
    has_caps = _write_ass(get_words(vdir), t0, dur, ass_path)

    # campaign watermark (PBM/Jason rule: clearly visible, NOT at the edge)
    wm = _load_creators().get(creator, {}).get("watermark", "")
    wm_path = (BASE / wm) if wm else None
    wm_on = bool(wm_path and wm_path.exists())

    print(f"✂ Cutting {_s_to_ts(t0)} +{dur:.0f}s → 9:16 "
          f"{'+ captions ' if has_caps else ''}{'+ watermark ' if wm_on else ''}…")
    vf = ("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
          "crop=1080:1920,boxblur=25:5[bg];"
          "[0:v]scale=1080:-2[fg];"
          "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1")
    if wm_on:
        vf += "[m];[1:v]scale=430:-1[wm];[m][wm]overlay=(W-w)/2:260"
    if has_caps:
        vf += f",subtitles=f={ass_path}:fontsdir={FONTS_DIR}"
    cmd = [_ffmpeg_with_libass(), "-y", "-ss", f"{t0:.2f}", "-i", str(vdir / "source.mp4")]
    if wm_on:
        cmd += ["-i", str(wm_path)]
    cmd += ["-t", f"{dur:.2f}", "-filter_complex", vf,
            "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
            str(final)]
    subprocess.run(cmd, check=True, capture_output=True)
    size_mb = final.stat().st_size / 1e6
    print(f"✅ {final}  ({size_mb:.1f} MB, {dur:.0f}s)  hook: {c['hook']}")
    try:  # track in the vault Clip Pipeline sheet (best-effort)
        from tools import clip_sheet
        clip_sheet.append(creator, c["hook"], final.stem,
                          notes=f"{dur:.0f}s · score {c['score']:.0f}")
    except Exception as e:  # noqa: BLE001
        print(f"⚠ sheet tracking skipped: {e}")
    return final


# ── pack (campaign-asset mode: provided footage only, e.g. Disney Moana) ─────

def pack(creator: str, src_dir: str) -> None:
    """Package campaign-provided assets into compliant 9:16 posts.

    For brand campaigns that BAN outside footage (Moana: assets-only, native
    audio contained per clip, 25s-2min, mandatory on-screen text). Each video in
    src_dir → blurred-bg vertical + creators.json 'overlay' lines burned at the
    top + audio kept as-is → registered in the sheet with a compliant caption.
    """
    cfg = _load_creators().get(creator, {})
    overlay = cfg.get("overlay", [])
    approved = cfg.get("approved_captions", [])
    mandatory = cfg.get("caption_suffix", "")
    out_dir = OUT_ROOT / creator
    out_dir.mkdir(parents=True, exist_ok=True)
    vids = sorted(Path(src_dir).expanduser().glob("*.mp4")) + \
           sorted(Path(src_dir).expanduser().glob("*.mov"))
    if not vids:
        raise SystemExit(f"no videos in {src_dir}")

    ass_path = None
    if overlay:
        big, *rest = overlay
        text = r"{\fs110}" + big + r"{\fs52}" + "".join(r"\N" + l for l in rest)
        ass_path = Path(src_dir).expanduser() / "_overlay.ass"
        ass_path.write_text(
            "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
            "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
            "MarginL, MarginR, MarginV, Encoding\n"
            "Style: Cap,Bangers,60,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
            "0,0,0,0,100,100,0,0,1,6,2,8,60,60,120,1\n\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            f"Dialogue: 0,0:00:00.00,9:59:59.00,Cap,,0,0,0,,{text}\n")

    for n, src in enumerate(vids, 1):
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(src)], capture_output=True, text=True)
        dur = float(probe.stdout.strip() or 0)
        if dur < 25:
            print(f"— skipping {src.name}: {dur:.0f}s < campaign 25s minimum")
            continue
        slug = re.sub(r"[^A-Za-z0-9]+", "_", src.stem).strip("_").lower()[:40]
        final = out_dir / f"{slug}.mp4"
        if final.exists():
            print(f"· already packed: {final.name}")
            continue
        vf = ("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
              "crop=1080:1920,boxblur=25:5[bg];"
              "[0:v]scale=1080:-2[fg];"
              "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1")
        if ass_path:
            vf += f",subtitles=f={ass_path}:fontsdir={FONTS_DIR}"
        cmd = [_ffmpeg_with_libass(), "-y", "-i", str(src)]
        if dur > 120:
            cmd += ["-t", "118"]                 # campaign max 2 min
        cmd += ["-filter_complex", vf, "-r", "30",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                str(final)]
        print(f"📦 Packing {src.name} ({dur:.0f}s)…")
        subprocess.run(cmd, check=True, capture_output=True)
        cap = approved[(n - 1) % len(approved)] if approved else src.stem
        hook = (cap + " " + mandatory).strip()
        try:
            from tools import clip_sheet
            clip_sheet.append(creator, hook, final.stem, notes=f"{min(dur,118):.0f}s · asset")
        except Exception as e:  # noqa: BLE001
            print(f"⚠ sheet tracking skipped: {e}")
        print(f"✅ {final}")


# ── stage (TikTok upload: file + caption preloaded, YOU click Post) ──────────

TIKTOK_PROFILE = str(BASE / ".browser_profile_tiktok")   # own profile — no clashes
UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?from=webapp"


def stage(target: str) -> None:
    """Open TikTok's upload page with the clip file + compliant caption already
    loaded. Mirrors the job-fill pattern: NOTHING is posted automatically — you
    review each one and click Post yourself (and pin the comment if the
    campaign requires it). `target` = a clip stem or a creator name (stages all
    of that creator's 🎬 Rendered clips one by one)."""
    from playwright.sync_api import sync_playwright
    from tools import clip_sheet
    creators = _load_creators()
    rows = clip_sheet.rows("Rendered")
    todo = [r for r in rows if r["clip"] == target] or \
           [r for r in rows if r["creator"] == target]
    if not todo:
        raise SystemExit(f"nothing 🎬 Rendered matches '{target}'")
    print(f"Staging {len(todo)} clip(s). A browser will open — first time, log "
          f"into the RIGHT TikTok account in it (session is remembered).")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            TIKTOK_PROFILE, headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1380, "height": 920})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for i, r in enumerate(todo):
            path = OUT_ROOT / r["creator"] / f"{r['clip']}.mp4"
            if not path.exists():
                print(f"✗ missing file: {path}")
                continue
            cfg = creators.get(r["creator"], {})
            tags = cfg.get("hashtags", f"#{r['creator']} #fyp #viral")
            caption = (r["hook"] + " " + tags).strip()
            page.goto(UPLOAD_URL, timeout=60000)
            page.wait_for_timeout(4000)
            if "/login" in page.url:
                input("→ Log into TikTok in the window, then press Enter here…")
                page.goto(UPLOAD_URL, timeout=60000)
                page.wait_for_timeout(4000)
            page.locator("input[type=file]").first.set_input_files(str(path))
            print(f"⬆ {r['clip']} uploading — waiting for the editor…")
            try:  # caption editor appears once the upload is accepted
                page.locator("div[contenteditable='true']").first.wait_for(timeout=60000)
                page.wait_for_timeout(2500)
                box = page.locator("div[contenteditable='true']").first
                box.click()
                page.keyboard.press("Meta+A")
                page.keyboard.press("Backspace")
                page.keyboard.insert_text(caption)
            except Exception as e:  # noqa: BLE001 — caption is still on the clipboard path
                print(f"⚠ couldn't auto-fill caption ({str(e)[:60]}) — paste it yourself:")
            print(f"\n📱 READY: {r['clip']}\n   caption: {caption[:110]}…\n"
                  f"   → review in the window, click POST, pin the campaign "
                  f"comment if required.")
            if i < len(todo) - 1:
                input("   Press Enter AFTER posting to stage the next clip…")
            else:
                input("   Last one — press Enter after posting to close…")
            try:
                clip_sheet.mark(r["clip"], "Posted")
                print(f"   ✓ marked 📱 Posted in the sheet")
            except Exception:
                pass
        ctx.close()


def cmd_stage(args):
    stage(args[0])


def _notify(msg: str) -> None:
    """Reach Taran: Telegram if possible, console always."""
    print(msg)
    try:
        from campaign_watch import telegram, _load_env
        _load_env()
        telegram(msg)
    except Exception:
        pass


def _dismiss_tour(page) -> None:
    """Kill TikTok Studio's first-time joyride tour overlay — it intercepts all
    pointer events and deadlocks every click until dismissed."""
    try:
        page.keyboard.press("Escape")
        page.evaluate(
            "document.querySelectorAll('#react-joyride-portal,"
            ".react-joyride__overlay,[data-test-id=overlay]')"
            ".forEach(e=>e.remove())")
    except Exception:
        pass


def _first_that_works(page, attempts, desc):
    """Try selector strategies until one clicks; else hand control to Taran —
    via terminal when interactive, via Telegram + a 90s window otherwise."""
    for fn in attempts:
        try:
            fn()
            return True
        except Exception:
            continue
    if sys.stdin.isatty():
        input(f"   ⚠ Couldn't {desc} automatically — do it in the window, then press Enter…")
    else:
        _notify(f"🖱 TikTok upload needs you: couldn't {desc} automatically — "
                f"please do it in the browser window on your Mac (you have ~90s).")
        page.wait_for_timeout(90000)
    return False


GROWTH_TAPES = BASE / "growth_tapes.json"
TIKTOK_PROFILE_GROWTH = str(BASE / ".browser_profile_tiktok_growth")  # growth-account login

def _growth_rows(key: str) -> list:
    """Growth-niche tapes (content_pipeline renders) live outside the clip sheet —
    synthesize autopost-shaped rows from growth_tapes.json. `key` is a tape id
    or the pseudo-creator 'growth'."""
    try:
        reg = json.loads(GROWTH_TAPES.read_text())
    except Exception:
        return []
    rows = []
    for t in reg.get("tapes", []):
        if "Rendered" not in t.get("status", ""):
            continue
        if key not in (t.get("id"), "growth"):
            continue
        rows.append({"clip": t["id"], "creator": "growth",
                     "hook": t.get("caption", t.get("title", t["id"])),
                     "__path": os.path.expanduser(t["file"])})
    return rows


def _growth_mark_posted(clip_id: str) -> None:
    try:
        reg = json.loads(GROWTH_TAPES.read_text())
        for t in reg.get("tapes", []):
            if t.get("id") == clip_id:
                t["status"] = "📱 Posted"
        GROWTH_TAPES.write_text(json.dumps(reg, indent=2, ensure_ascii=False))
    except Exception:
        pass


def autopost(creator: str, mode: str = "schedule", gap_min: int = 20) -> None:
    """Upload a creator's 🎬 Rendered clips to TikTok without per-clip babysitting.

    mode="private":  posts with visibility 'Only you' — review on TikTok, flip
                     each to public yourself. Sheet stays 🎬 Rendered.
    mode="schedule": uses TikTok's NATIVE scheduler, spacing posts gap_min apart
                     (first one ~40 min out). Sheet rows flip to 📱 Posted.
    Telegrams a summary when the batch is done. Any control the automation
    can't find is handed to you in the window (script pauses, you click, Enter).
    """
    from datetime import datetime, timedelta
    from playwright.sync_api import sync_playwright
    from tools import clip_sheet
    creators = _load_creators()
    rows = clip_sheet.rows("Rendered")
    todo = [r for r in rows if r["clip"] == creator] or \
           [r for r in rows if r["creator"] == creator]
    if not todo:
        todo = _growth_rows(creator)   # growth tapes live outside the clip sheet
    if not todo:
        raise SystemExit(f"nothing 🎬 Rendered matches '{creator}'")
    creator = todo[0]["creator"]
    # Growth tapes post from a SEPARATE TikTok account → separate browser profile
    # (first upload shows the login page once; the session persists after that).
    profile = TIKTOK_PROFILE_GROWTH if any(r.get("__path") for r in todo) else TIKTOK_PROFILE
    print(f"{mode.upper()} upload of {len(todo)} {creator} clip(s). Browser opening — "
          f"log into the RIGHT TikTok account if asked.")
    done, t0 = [], datetime.now() + timedelta(minutes=40)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            profile, headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1380, "height": 920})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for i, r in enumerate(todo):
            path = Path(r["__path"]) if r.get("__path") else \
                   OUT_ROOT / r["creator"] / f"{r['clip']}.mp4"
            if not path.exists():
                print(f"✗ missing {path}")
                continue
            cfg = creators.get(r["creator"], {})
            # growth tapes carry their full caption (tags included) in the registry
            tags = "" if r.get("__path") else cfg.get("hashtags", f"#{r['creator']} #fyp #viral")
            caption = (r["hook"] + " " + tags).strip()
            page.goto(UPLOAD_URL, timeout=60000)
            page.wait_for_timeout(4000)
            if "/login" in page.url:
                if sys.stdin.isatty():
                    input("→ Log into TikTok in the window, then press Enter…")
                else:
                    try:
                        page.bring_to_front()
                    except Exception:
                        pass
                    _notify("🔑 TikTok upload paused: a Chromium window is open on "
                            "your Mac (check your other desktop Spaces if you don't "
                            "see it) — log into the TikTok account you're posting "
                            "from. I'll continue automatically once you're in "
                            "(waiting up to 12 min).")
                    for _ in range(144):                     # poll ~12 min
                        page.wait_for_timeout(5000)
                        if "/login" not in page.url:
                            break
                page.goto(UPLOAD_URL, timeout=60000)
                page.wait_for_timeout(4000)
                if "/login" in page.url:
                    raise RuntimeError("still not logged into TikTok — aborting batch")
            _dismiss_tour(page)
            page.locator("input[type=file]").first.set_input_files(str(path))
            print(f"⬆ [{i+1}/{len(todo)}] {r['clip']} uploading…")
            page.locator("div[contenteditable='true']").first.wait_for(timeout=120000)
            page.wait_for_timeout(3000)
            _dismiss_tour(page)
            box = page.locator("div[contenteditable='true']").first
            box.click()
            page.keyboard.press("Meta+A")
            page.keyboard.press("Backspace")
            page.keyboard.insert_text(caption)
            page.wait_for_timeout(1000)

            if mode == "draft":
                pass                              # nothing to configure — just save
            elif mode == "private":
                _first_that_works(page, [
                    lambda: page.get_by_text("Everyone", exact=True).first.click(timeout=4000),
                    lambda: page.locator("[class*=Select]").first.click(timeout=4000),
                ], "open the visibility dropdown")
                _first_that_works(page, [
                    lambda: page.get_by_text("Only you", exact=True).first.click(timeout=4000),
                ], "pick 'Only you'")
            else:
                when = t0 + timedelta(minutes=gap_min * i)
                _first_that_works(page, [
                    lambda: page.get_by_text("Schedule", exact=True).first.click(timeout=4000),
                    lambda: page.get_by_role("radio", name=re.compile("Schedule", re.I)).first.click(timeout=4000),
                ], "switch to Schedule")
                page.wait_for_timeout(1200)

                def _set_time():
                    f = page.locator("input").filter(has_not=page.locator("[type=file]")).last
                    f.click()
                    page.keyboard.press("Meta+A")
                    page.keyboard.insert_text(when.strftime("%H:%M"))
                    page.keyboard.press("Escape")
                if not _first_that_works(page, [_set_time],
                                         f"set the time to {when.strftime('%H:%M')}"):
                    pass
                print(f"   🕐 scheduling for {when.strftime('%H:%M')}")

            # the final button depends on mode: Save draft, Post, or Schedule
            if mode == "draft":
                _first_that_works(page, [
                    lambda: page.get_by_role("button", name=re.compile(r"draft", re.I)).last.click(timeout=6000),
                    lambda: page.get_by_text("Save draft", exact=False).last.click(timeout=6000),
                ], "click the Save draft button")
            else:
                _first_that_works(page, [
                    lambda: page.get_by_role("button", name=re.compile(r"^(Post|Schedule)$", re.I)).last.click(timeout=6000),
                    lambda: page.locator("[data-e2e='post_video_button']").click(timeout=6000),
                ], "click the Post/Schedule button")
            page.wait_for_timeout(7000)          # let the submit land
            done.append((r["clip"], caption[:60]))
            if mode == "schedule":
                try:
                    if r.get("__path"):
                        _growth_mark_posted(r["clip"])
                    else:
                        clip_sheet.mark(r["clip"], "Posted")
                except Exception:
                    pass
            print(f"   ✓ {r['clip']} done")
        if mode == "draft":
            # Taran reviews the saved draft in the open window — do NOT close it.
            # The script waits here until he closes the window himself (the next
            # Draft button stays 409-blocked until then).
            print("Draft saved — leaving the browser window open for review; "
                  "close it yourself when done.")
            try:
                _notify("📱 Draft saved — the browser window is staying OPEN so you "
                        "can review or post it. Close the window when you're done "
                        "(the next Draft button won't run until you do).")
            except Exception:
                pass
            while True:
                try:
                    if not ctx.pages:
                        break
                    ctx.pages[0].wait_for_timeout(2000)
                except Exception:
                    break
        try:
            ctx.close()
        except Exception:
            pass

    msg = (f"🎬 TikTok {mode} batch finished — {len(done)}/{len(todo)} {creator} "
           f"clip(s) uploaded" +
           (f", spaced {gap_min} min apart (first at {t0.strftime('%H:%M')})."
            if mode == "schedule" else
            " as DRAFTS — open TikTok Studio → Posts → Drafts, review each and post."
            if mode == "draft" else
            " as PRIVATE — review on TikTok and flip each to public.") +
           ("\nStatuses set to 📱 Posted." if mode == "schedule" else "") +
           "\n\n" + "\n".join(f"• {c} — {cap}…" for c, cap in done) +
           ("\n\n⚠ Moana rule: pin the Fandango link in each comment section "
            "once a post is live." if creator == "moana" else ""))
    try:
        from campaign_watch import telegram, _load_env
        _load_env()
        telegram(msg)
        print("📨 Telegram sent.")
    except Exception as e:  # noqa: BLE001
        print(f"⚠ telegram failed: {e}\n{msg}")


def cmd_autopost(args):
    autopost(args[0], args[1] if len(args) > 1 else "schedule",
             int(args[2]) if len(args) > 2 else 20)


# ── watchlist (channels to clip for) ─────────────────────────────────────────

CREATORS_FILE = BASE / "creators.json"


def _load_creators() -> dict:
    if CREATORS_FILE.exists():
        return json.loads(CREATORS_FILE.read_text())
    return {}


def check(limit: int = 5) -> None:
    """List latest uploads per watched creator; flag ones not yet clipped."""
    creators = _load_creators()
    if not creators:
        raise SystemExit(f"No watchlist yet — create {CREATORS_FILE}")
    for name, cfg in creators.items():
        print(f"\n👤 {name}  ({cfg['url']})")
        r = subprocess.run(
            ["yt-dlp", "--flat-playlist", "-I", f"1:{limit}",
             "--print", "%(id)s|%(duration)s|%(title)s", cfg["url"]],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            print(f"  ⚠ {r.stderr.strip().splitlines()[-1][:100] if r.stderr else 'failed'}")
            continue
        for line in r.stdout.strip().splitlines():
            vid, dur, title = (line.split("|", 2) + ["", ""])[:3]
            done = (CACHE / name / vid).exists()
            mins = f"{float(dur) / 60:.0f}m" if dur not in ("", "NA") else "live?"
            mark = "  ✔ clipped" if done else "  🆕 NEW"
            print(f"  {vid}  {mins:>5}  {title[:55]}{mark}")


def subs(limit: int = 15) -> None:
    """Latest uploads from YOUR YouTube subscriptions (via Chrome cookies)."""
    r = subprocess.run(
        ["yt-dlp", "--cookies-from-browser", "chrome",
         "--flat-playlist", "-I", f"1:{limit}",
         "--print", "%(id)s|%(channel)s|%(title)s",
         "https://www.youtube.com/feed/subscriptions"],
        capture_output=True, text=True, timeout=180,
    )
    if r.returncode != 0:
        err = r.stderr.strip().splitlines()[-1] if r.stderr else "failed"
        raise SystemExit(
            f"Couldn't read subscriptions feed: {err[:200]}\n"
            "Chrome cookie decryption needs Keychain access — approve the "
            "prompt, make sure Chrome is your logged-in browser, or add "
            "channels to creators.json and use `check` instead."
        )
    print("📺 Your subscriptions — latest uploads:")
    for line in r.stdout.strip().splitlines():
        vid, channel, title = (line.split("|", 2) + ["", ""])[:3]
        print(f"  {vid}  {channel[:20]:20}  {title[:55]}")


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_fetch(args):
    fetch(args[0], args[1])


def cmd_find(args):
    find(args[0], args[1] if len(args) > 1 else None)


def cmd_cut(args):
    cut(args[0], args[1], int(args[2]))


def cmd_auto(args):
    url, creator = args[0], args[1]
    n = int(args[2]) if len(args) > 2 else 3
    vdir = fetch(url, creator)
    candidates = find(creator, vdir.name)
    for i in range(1, min(n, len(candidates)) + 1):
        cut(creator, vdir.name, i)


def cmd_list(args):
    creator = args[0]
    for vdir in sorted((CACHE / creator).glob("*/")):
        info = _load_info(vdir)
        cand = vdir / "candidates.json"
        n = len(json.loads(cand.read_text())) if cand.exists() else 0
        print(f"{vdir.name}  {info.get('title','?')[:60]}  ({n} candidates)")


def daily(max_new_per_creator: int = 1, clips_per_video: int = 3) -> None:
    """The 'generate clips for the day' entry point.

    For every ACTIVE creator in creators.json: find their newest unclipped
    upload, run fetch→find→cut on it, track everything in the vault sheet,
    then print the posting queue.
    """
    creators = _load_creators()
    active = {k: v for k, v in creators.items() if v.get("active")}
    if not active:
        raise SystemExit("No active creators in creators.json (set \"active\": true)")
    rendered = []
    for name, cfg in active.items():
        print(f"\n👤 {name} — checking uploads…")
        r = subprocess.run(
            ["yt-dlp", "--flat-playlist", "-I", "1:6",
             "--print", "%(id)s|%(duration)s|%(title)s", cfg["url"]],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            print("  ⚠ channel listing failed — skipping")
            continue
        is_twitch = "twitch.tv" in cfg["url"]
        new_done = 0
        for line in r.stdout.strip().splitlines():
            if new_done >= max_new_per_creator:
                break
            vid, dur, title = (line.split("|", 2) + ["", ""])[:3]
            if (CACHE / name / vid).exists():
                continue
            if dur in ("", "NA"):
                continue
            if is_twitch:
                if float(dur) < 600:
                    continue          # skip tiny VODs; hours-long is expected
            elif not (240 <= float(dur) <= 5400):
                continue  # YouTube: skip live/short uploads and >90min stream VODs
            print(f"  🎬 New: {title[:60]}")
            try:
                src_url = (f"https://www.twitch.tv/videos/{vid.lstrip('v')}"
                           if is_twitch else f"https://www.youtube.com/watch?v={vid}")
                fetch(src_url, name,
                      section="00:00:00-01:00:00" if is_twitch else None)
                candidates = find(name, vid)
                for i in range(1, min(clips_per_video, len(candidates)) + 1):
                    rendered.append(cut(name, vid, i))
                new_done += 1
            except Exception as e:  # noqa: BLE001 — one bad video shouldn't kill the run
                print(f"  ⚠ {vid} failed: {e}")
    print(f"\n{'─' * 60}\n🎬 Rendered {len(rendered)} new clip(s) this run.")
    queue()


def queue() -> None:
    """Posting queue: everything rendered but not yet posted, with campaign links."""
    try:
        from tools import clip_sheet
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"sheet unavailable: {e}")
    creators = _load_creators()
    pending = clip_sheet.rows("Rendered")
    if not pending:
        print("📭 Posting queue is empty — run `daily` to generate clips.")
        return
    print(f"\n📱 POSTING QUEUE — {len(pending)} clip(s) ready:")
    for r in pending:
        cfg = creators.get(r["creator"], {})
        campaign = cfg.get("campaign", "—")
        # some campaigns ban unaffiliated hashtags — per-creator override via
        # creators.json "hashtags" ("" = clean caption)
        tags = cfg.get("hashtags", f"#{r['creator']} #fyp #viral")
        path = OUT_ROOT / r["creator"] / f"{r['clip']}.mp4"
        print(f"\n  {r['clip']}  ({r['creator']}, {r['date']})")
        print(f"    hook:    {r['hook']}")
        print(f"    caption: {(r['hook'] + ' ' + tags).strip()}")
        print(f"    file:    {path}")
        print(f"    submit:  {campaign}")
    print("\nAfter posting:  python3 clip_pipeline.py mark <clip> posted <url>")
    print("After submit:   python3 clip_pipeline.py mark <clip> submitted")
    subprocess.run(["open", str(OUT_ROOT)], check=False)


def cmd_check(args):
    check(int(args[0]) if args else 5)


def cmd_subs(args):
    subs(int(args[0]) if args else 15)


def cmd_daily(args):
    daily(int(args[0]) if args else 1, int(args[1]) if len(args) > 1 else 3)


def cmd_queue(args):
    queue()


def cmd_mark(args):
    from tools import clip_sheet
    ok = clip_sheet.mark(args[0], args[1], args[2] if len(args) > 2 else None)
    print("✅ updated" if ok else f"⚠ clip '{args[0]}' not found in sheet")


def cmd_pack(args):
    pack(args[0], args[1])


def ingest(creator: str, src: str) -> None:
    """Register a LOCAL video file (downloaded campaign asset, screen recording,
    etc.) as a pipeline source so find/cut work on it — whisper transcribes if
    no subs exist. Usage: ingest <creator> <path/to/video.mp4>"""
    src_p = Path(src).expanduser()
    if not src_p.exists():
        raise SystemExit(f"no such file: {src}")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", src_p.stem).strip("_").lower()[:40]
    vdir = CACHE / creator / slug
    vdir.mkdir(parents=True, exist_ok=True)
    dest = vdir / "source.mp4"
    if not dest.exists():
        import shutil
        shutil.copyfile(src_p, dest)
    (vdir / "source.info.json").write_text(json.dumps({"title": src_p.stem}))
    print(f"✔ ingested as {creator}/{slug} — next: find {creator} {slug}")


def cmd_ingest(args):
    ingest(args[0], args[1])


def cmd_enhance(args):
    from clip_enhance import cmd_enhance as _ce
    _ce(args)


def cmd_prep(args):
    from clip_prep import prep
    prep(args[0], args[1], int(args[2]))


COMMANDS = {"fetch": cmd_fetch, "find": cmd_find, "cut": cmd_cut,
            "enhance": cmd_enhance, "prep": cmd_prep,
            "auto": cmd_auto, "list": cmd_list,
            "check": cmd_check, "subs": cmd_subs,
            "daily": cmd_daily, "queue": cmd_queue, "mark": cmd_mark,
            "pack": cmd_pack, "stage": cmd_stage, "autopost": cmd_autopost,
            "ingest": cmd_ingest}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    sys.exit(COMMANDS[sys.argv[1]](sys.argv[2:]) or 0)
