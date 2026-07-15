#!/usr/bin/env python3
"""
clip_enhance.py — creative edit pass on top of clip_pipeline candidates. v3.

v3 is modeled on a measured study of the TOP clips from Ron / Lacy / Jason /
PBM / Marlon TikToks (2026-07-08, 1M-130M views each). What they actually do:

  • FILL THE FRAME — facecam or action region cropped to full 9:16; the
    blurred-letterbox look never appears on big clips. Layout (facecam box /
    action box) is auto-detected once per video via claude vision, cached,
    and overridable per creator in creators.json ("layout").
  • ONE static context/quote caption (TikTok-native white sans, mid-frame),
    NOT word-by-word karaoke — none of the top 10 used karaoke captions.
  • Very few cuts (top clips: 0-0.2 cuts/sec) — each cut is meaningful:
    reframe face↔action following the moment, a punch-in on the beat, the
    b-roll payoff. Dead-air is trimmed (Submagic-style) but the pacing
    stays calm.
  • Slow push-in (Ken Burns) during builds; slow-mo / freeze only ON the
    payoff; ≤2 SFX per clip; music bed ducked under speech for emotional
    clips (aura), swelling at the payoff.
  • B-roll payoff montage (`broll=<file>`) with the caption persisting —
    the ron1 "anime payoff" pattern.

Usage:
    python3 clip_pipeline.py enhance <creator> <video_id> <n> [meme|aura] [broll=<file>]

Output: ~/Desktop/Clips/<creator>/<video_id>_c<n>x.mp4
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from pathlib import Path

from clip_pipeline import (
    BASE, FONTS_DIR, MAX_CLIP_S, OUT_ROOT,
    _ass_ts, _ffmpeg_with_libass, _load_creators, _s_to_ts, _video_dir,
    get_words,
)

SFX_DIR = BASE / "sfx"
MUSIC_DIR = BASE / "music"
DEADAIR_GAP_S = 0.9
DEADAIR_PAD_S = 0.2
PULSE_S = 0.35
MAX_SEGMENTS = 24
MIN_FACE_W_PX = 220          # facecam narrower than this upscales too soft

SFX = {  # menu name → (file, mix volume, (trim_start, trim_dur) | None)
    "boom":    ("vine-boom.mp3",      1.0, None),
    "bruh":    ("bruh.mp3",           0.9, None),
    "scratch": ("record-scratch.mp3", 0.8, (0, 1.2)),
    "whoosh":  ("whoosh-real.mp3",    0.6, None),
    "ding":    ("ding-real.mp3",      0.7, (0, 1.4)),
    "riser":   ("riser-real.mp3",     0.5, (12.5, 4.0)),
    "aura":    ("aura-sound.mp3",     0.9, None),
}

# ── templates — fixed recipes distilled from the 25-clip study ───────────────
# Each is a proven 1M-130M-view pattern. claude only FILLS a template
# (caption, beat times, payoff); the recipe itself is not negotiable.

TEMPLATES = {
    "game_bubble": {   # ron game clips 3-8M: action full-frame + cam bubble
        "when": "gameplay/content stream with a small facecam — the play AND "
                "the reaction both matter",
        "needs_pip": True,
        "rules": "Full-frame action, cam bubble always visible. A subtle punch "
                 "(1.05-1.10) per real beat, one per 4-6s MAX. Freeze 0.6-0.9s "
                 "+ boom ON the punchline word.",
        "events": ("punch", "freeze"),
        "max_events": 5, "deadair": True, "music": False, "payoff_grade": None,
        "broll": False,
    },
    "reaction_hold": {  # pbm3 / lacy / marlon 1-113M: hold the moment
        "when": "ONE person or one moment carries it — monologue, confession, "
                "stage bit, emotional beat. Cutting would destroy intimacy",
        "needs_pip": False,
        "rules": "0-2 events TOTAL. A slow push starting ~1s before the "
                 "emotional peak is the whole edit. Optionally ONE subtle "
                 "punch (1.05-1.10) at the payoff word. NO freezes, NO "
                 "reframes. Natural pauses stay (comedic/dramatic timing).",
        "events": ("push", "punch"),
        "max_events": 2, "deadair": False, "music": True, "payoff_grade": "bw",
        "broll": True,
    },
    "irl_beats": {     # jason IRL 2-5.7M: cut on each new beat
        "when": "IRL scene with several people/beats (street, gym, group)",
        "needs_pip": False,
        "rules": "Full-frame scene. Subtle punch (≤1.10) on each NEW beat (new "
                 "speaker, new reaction) — about one per 4-5s. Optional freeze "
                 "+ boom on the punchline.",
        "events": ("punch", "freeze"),
        "max_events": 6, "deadair": True, "music": False, "payoff_grade": None,
        "broll": False,
    },
    "hard_motivation": {  # goggins/mentzer edit genre (1-7M): B&W + phrase caps
        "when": "a motivational speech / hard-truth monologue (Goggins, "
                "Mentzer, discipline content) — the WORDS are the product",
        "needs_pip": False,
        "rules": "Whole clip is crushed black & white. Captions are the "
                 "spoken PHRASES, centered, all-caps, one hard keyword "
                 "highlighted red per phrase (list those in emphasis). "
                 "EXACTLY ONE event: a slow push through the whole build — "
                 "NO mid-clip cuts, zooms or slow-mo, the words carry it. "
                 "Music bed swells at payoff_t. Natural pauses STAY. B-roll "
                 "cutaway (gym/training) lands at the payoff.",
        "events": ("push",),
        "max_events": 1, "deadair": False, "music": True,
        "music_pref": "brazilian-phonk",
        "payoff_grade": "bw_full", "broll": True, "caption_mode": "phrases",
    },
    "quote_aura": {    # ron1 6.1M: iconic-line edit (b-roll payoff if provided)
        "when": "one ICONIC line/moment with meme/emotional resonance",
        "needs_pip": False,
        "rules": "Quote caption over the moment. ≤2 events: a slow push "
                 "through the build, slow-mo 1-2s INTO the payoff. Music "
                 "carries it; grade turns dark at the payoff; a matched "
                 "b-roll cutaway lands AT the payoff.",
        "events": ("push", "slowmo", "punch"),
        "max_events": 3, "deadair": False, "music": True, "payoff_grade": "dark",
        "broll": True,
    },
}

EDL_PROMPT = """You are filling a proven clip-editing TEMPLATE for a streamer clip. \
The template recipes are distilled from 25 clips with 1M-130M views — your job is \
to pick the right one and fill its slots precisely. Values render literally.

CLIP: "{hook}" — {dur:.0f}s, creator "{creator}".
WHY IT WAS CLIPPED: {reason}

WORD-TIMED TRANSCRIPT (seconds:word):
{transcript}

MEASURED AUDIO ENERGY (the clip's REAL beats — anchor event times to these):
- loudest spikes (yell/laugh/impact): {spikes}
- quiet lulls: {lulls}

AVAILABLE FRAMES:
{frames_note}

TEMPLATES{tmpl_note}:
{tmpl_menu}

Respond STRICT JSON only (no fences):
{{
 "template": "<name>",
 "caption": "ONE static line, 4-9 words — scene-setting third person OR the iconic \
quote in double quotes. No emojis, no hashtags.",
 "payoff_t": <seconds — THE moment, anchored to a measured spike>,
 "music": {music_menu},   // only if the template allows music, else "none"
 "broll_query": "3-6 word YouTube search for a cutaway matching the payoff's energy \
(e.g. 'itachi aura edit', 'crowd goes wild reaction'; for motivation: 'gym training \
dark cinematic', 'running alone rain cinematic') — or null if nothing fits",
 "emphasis": ["hard", "keywords"],  // hard_motivation only: 4-8 single words from \
the transcript that hit hardest — they render RED in the captions. Else [].
 "events": [   // obey the template's rules; zooms subtle (1.05-1.12)
   {{"t": 9.0, "type": "punch",  "zoom": 1.08}},
   {{"t": 3.0, "type": "push",   "dur": 4.0}},
   {{"t": 12.0,"type": "slowmo", "dur": 1.5}},
   {{"t": 14.0,"type": "freeze", "dur": 0.8, "sfx": "boom"}}
 ]
}}
Rules: first event ≥1s in, none in last 0.8s, ≥1.5s apart, times aligned to word \
starts or measured spikes. ≤2 events carry sfx ({sfx_menu}); riser only 2-4s before \
payoff_t."""


_SFX_SYNTH = {
    "boom": ("sine=f=54:d=0.8",
             "afade=t=in:d=0.004,afade=t=out:st=0.1:d=0.7,bass=g=10:f=70,volume=2.4"),
    "whoosh": ("anoisesrc=color=pink:d=0.55",
               "highpass=f=350,lowpass=f=3800,"
               "afade=t=in:d=0.28,afade=t=out:st=0.28:d=0.27,volume=0.9"),
}


def _sfx_path(name: str) -> Path | None:
    real = SFX_DIR / SFX[name][0] if name in SFX else None
    if real and real.exists():
        return real
    synth = SFX_DIR / f"{name}.wav"
    if synth.exists():
        return synth
    if name in _SFX_SYNTH:
        src, af = _SFX_SYNTH[name]
        SFX_DIR.mkdir(exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", src, "-af", af,
                        "-ar", "44100", str(synth)], check=True, capture_output=True)
        return synth
    return None


def _music_menu() -> list[str]:
    MUSIC_DIR.mkdir(exist_ok=True)
    return sorted(p.stem for p in MUSIC_DIR.glob("*.mp3"))


# ── clip prep: work file, energy, layout ─────────────────────────────────────

def _work_file(vdir: Path, idx: int, t0: float, dur: float) -> Path:
    work = vdir / f"work_c{idx}.mp4"
    if work.exists():   # window may have changed (sentence extension etc.)
        pr = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                             "format=duration", "-of", "csv=p=0", str(work)],
                            capture_output=True, text=True)
        if abs(float(pr.stdout.strip() or 0) - dur) > 0.5:
            work.unlink()
    if not work.exists():
        # -r 30 is load-bearing: zoompan emits fps=30, so a 60fps work file
        # would double push-segment durations and shift every later cut
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{t0:.2f}", "-t", f"{dur:.2f}",
             "-i", str(vdir / "source.mp4"), "-r", "30",
             "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
             "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
             str(work)], check=True, capture_output=True)
    return work


def _src_dims(work: Path) -> tuple[int, int]:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v",
                        "-show_entries", "stream=width,height", "-of", "csv=p=0",
                        str(work)], capture_output=True, text=True)
    w, h = r.stdout.strip().split(",")[:2]
    return int(w), int(h)


def _detect_layout(work: Path, vdir: Path, idx: int, dur: float,
                   creator_cfg: dict) -> dict:
    """{'face': [fx,fy,fw,fh] | None, 'action': [...] | None} — creators.json
    'layout' wins; else claude vision on a mid-clip frame, cached per video."""
    if isinstance(creator_cfg.get("layout"), dict):
        return creator_cfg["layout"]
    cache = vdir / "layout.json"
    if cache.exists():
        return json.loads(cache.read_text())
    frame = vdir / f"layout_frame.png"
    subprocess.run(["ffmpeg", "-y", "-ss", f"{dur / 2:.1f}", "-i", str(work),
                    "-frames:v", "1", str(frame)], check=True, capture_output=True)
    prompt = (f"Read the image {frame} — a frame from a livestream. Identify: "
              '(1) the streamer facecam/webcam rectangle, (2) the main '
              'action/content region. Respond STRICT JSON only: '
              '{"face": [x, y, w, h], "action": [x, y, w, h]} as FRACTIONS '
              "of image width/height (0-1, two decimals). If no facecam is "
              "visible use null for face. If the whole frame IS the person "
              "(IRL stream), face covers them and action is null.")
    r = subprocess.run(["claude", "-p", "--model", "sonnet",
                        "--allowedTools", "Read"],
                       input=prompt, capture_output=True, text=True, timeout=180)
    layout = {"face": None, "action": None}
    m = re.search(r"\{.*\}", r.stdout, re.S)
    if m:
        try:
            got = json.loads(m.group(0))
            for k in ("face", "action"):
                v = got.get(k)
                if (isinstance(v, list) and len(v) == 4
                        and all(isinstance(x, (int, float)) for x in v)
                        and 0.01 <= v[2] <= 1 and 0.01 <= v[3] <= 1):
                    layout[k] = [round(float(x), 3) for x in v]
        except json.JSONDecodeError:
            pass
    cache.write_text(json.dumps(layout))
    return layout


def _energy(work: Path, dur: float) -> tuple[list[float], list[float]]:
    tmp = work.parent / "rms.txt"
    subprocess.run(
        ["ffmpeg", "-i", str(work), "-af",
         f"astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats."
         f"Overall.RMS_level:file={tmp}", "-f", "null", "-"],
        capture_output=True)
    buckets: dict[int, list[float]] = {}
    t = 0.0
    for line in tmp.read_text(errors="ignore").splitlines():
        if "pts_time" in line:
            t = float(line.split("pts_time:")[1])
        elif "RMS_level" in line:
            v = line.split("=")[1].strip()
            if v != "-inf":
                buckets.setdefault(int(t * 4), []).append(float(v))
    tmp.unlink(missing_ok=True)
    if not buckets:
        return [], []
    curve = sorted((k / 4.0, sum(v) / len(v)) for k, v in buckets.items())
    vals = sorted(v for _, v in curve)
    hi = vals[int(len(vals) * 0.88)]
    lo = vals[int(len(vals) * 0.15)]
    spikes, lulls, last_s, last_l = [], [], -2.0, -3.0
    for tt, v in curve:
        if v >= hi and tt - last_s >= 1.5 and 0.5 < tt < dur - 0.5:
            spikes.append(round(tt, 1)); last_s = tt
        elif v <= lo and tt - last_l >= 2.5 and 0.5 < tt < dur - 0.5:
            lulls.append(round(tt, 1)); last_l = tt
    return spikes[:8], lulls[:5]


def _clean_word(word: str) -> str:
    w = html.unescape(word)
    return re.sub(r"[{}\\]|>>+", "", w).strip()


def _clip_words(words: list[dict], t0: float, dur: float) -> list[dict]:
    out = []
    for w in words:
        if not t0 <= w["start"] < t0 + dur:
            continue
        word = _clean_word(w["word"])
        if word:
            out.append({"word": word,
                        "start": max(w["start"] - t0, 0.0),
                        "end": min(w["end"] - t0, dur)})
    return out


# ── EDL ──────────────────────────────────────────────────────────────────────

def _claude_json_obj(prompt: str, tools: str | None = None) -> dict:
    cmd = ["claude", "-p", "--model", "sonnet"]
    if tools:
        cmd += ["--allowedTools", tools]
    for attempt in (1, 2):     # claude -p fails transiently now and then
        r = subprocess.run(cmd, input=prompt, capture_output=True,
                           text=True, timeout=240)
        m = re.search(r"\{.*\}", r.stdout, re.S) if r.returncode == 0 else None
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        if attempt == 1:
            print(f"  ↻ claude retry ({(r.stderr or 'no json').strip()[:80]})")
    return {}


def _fallback_edl(dur: float, spikes: list[float], hook: str) -> dict:
    p = spikes[-1] if spikes else dur * 0.7
    return {"style": "meme", "template": "reaction_hold", "caption": hook,
            "payoff_t": p, "music": "none", "deadair": False,
            "payoff_grade": None, "caption_mode": "context",
            "start_frame": "action", "broll_query": "",
            "events": [{"t": 1.2, "type": "push", "dur": min(6.0, dur - 3),
                        "zoom": 1.08, "frame": None, "sfx": "none"}],
            "emphasis": set()}


def get_edl(creator: str, cand: dict, cwords: list[dict], dur: float,
            spikes: list[float], lulls: list[float], layout: dict,
            force_style: str | None) -> dict:
    transcript = "\n".join(f"{w['start']:.1f}:{w['word']}" for w in cwords)
    frames_note = ["The frame is a fixed wide crop of the content"
                   + (" with the facecam always visible as a small corner "
                      "bubble." if layout.get("face") and layout.get("action")
                      else ".")
                   + " There are NO reframes — punches/pushes are subtle "
                     "content zooms only."]
    have_face = bool(layout.get("face"))
    have_action = bool(layout.get("action"))
    allowed = {n: t for n, t in TEMPLATES.items()
               if not t["needs_pip"] or (have_face and have_action)}
    tmpl_menu = "\n".join(f'- "{n}" — use when: {t["when"]}. RECIPE: {t["rules"]}'
                          for n, t in allowed.items())
    prompt = EDL_PROMPT.format(
        hook=cand["hook"], dur=dur, creator=creator,
        reason=cand.get("reason", ""), transcript=transcript,
        spikes=", ".join(f"{t}s" for t in spikes) or "none measured",
        lulls=", ".join(f"{t}s" for t in lulls) or "none",
        frames_note="\n".join(frames_note),
        tmpl_note=f' (FORCED: use "{force_style}")'
                  if force_style in allowed else "",
        tmpl_menu=tmpl_menu,
        music_menu=json.dumps(_music_menu() + ["none"]),
        sfx_menu=", ".join(SFX))
    raw = _claude_json_obj(prompt)
    if not raw or "events" not in raw:
        print("  ⚠ claude EDL unavailable — fallback")
        return _fallback_edl(dur, spikes, cand["hook"])

    tname = force_style if force_style in allowed else str(raw.get("template", ""))
    if tname not in allowed:
        tname = next(iter(allowed))
    tmpl = TEMPLATES[tname]
    style = "aura" if tname == "quote_aura" else "meme"
    word_starts = [w["start"] for w in cwords]

    def _snap(t: float) -> float:
        """Cuts land on speech boundaries, not mid-word."""
        if not word_starts:
            return t
        near = min(word_starts, key=lambda s: abs(s - t))
        return near if abs(near - t) <= 0.6 else t

    events, last_t, n_freeze, n_slow = [], -9.0, 0, 0
    for e in sorted(raw.get("events", []), key=lambda e: float(e.get("t", 0) or 0)):
        try:
            t = _snap(float(e["t"]))
        except (KeyError, TypeError, ValueError):
            continue
        typ = str(e.get("type", ""))
        if typ not in tmpl["events"]:          # template recipe is law
            continue
        if len(events) >= tmpl["max_events"]:
            break
        if not 0.8 <= t <= dur - 0.9 or t - last_t < 1.0:
            continue
        if typ == "freeze":
            if n_freeze:
                continue
            n_freeze += 1
        if typ == "slowmo":
            if n_slow:
                continue
            n_slow += 1
        events.append({
            "t": round(t, 2), "type": typ,
            "frame": None,                     # face reframes disabled

            "zoom": min(max(float(e.get("zoom", 1.08) or 1.08), 1.0), 1.15),
            "dur": min(max(float(e.get("dur", 1.0) or 1.0), 0.4), 8.0),
            "sfx": (str(e.get("sfx", "none")).lower()
                    if str(e.get("sfx", "none")).lower() in SFX else "none")})
        last_t = t
    if sum(1 for e in events if e["sfx"] != "none") > 2:   # sfx restraint
        keep = 0
        for e in events:
            if e["sfx"] != "none":
                keep += 1
                if keep > 2:
                    e["sfx"] = "none"
    payoff = min(max(_snap(float(raw.get("payoff_t") or dur * 0.7)), 1.0), dur - 0.5)
    music = str(raw.get("music", "none")) if tmpl["music"] else "none"
    pref = tmpl.get("music_pref")
    if tmpl["music"] and pref and pref in _music_menu():
        music = pref
    bq = raw.get("broll_query")
    broll_query = str(bq).strip()[:60] if bq and str(bq).lower() != "null" else ""
    caption = re.sub(r"[{}\\#]", "", str(raw.get("caption", "")))[:80].strip()
    emphasis = {str(w).strip().lower().strip(".,!?\"'")
                for w in raw.get("emphasis", []) if str(w).strip()}
    return {"style": "aura" if tmpl["music"] else style, "template": tname,
            "caption": caption or cand["hook"],
            "payoff_t": payoff,
            "music": music if music in _music_menu() else "none",
            "deadair": tmpl["deadair"],
            "payoff_grade": tmpl["payoff_grade"],
            "caption_mode": tmpl.get("caption_mode", "context"),
            "broll_query": broll_query if tmpl.get("broll") else "",
            "start_frame": "action",
            "events": events, "emphasis": emphasis}


# ── b-roll from YouTube (payoff cutaway, cached) ─────────────────────────────

BROLL_DIR = BASE / "broll_yt"
BROLL_MAX_S = 5.5


def fetch_broll(query: str) -> Path | None:
    """Search YouTube for a matching cutaway, download a short section,
    normalize to a muted 30fps snippet. Cached by query slug."""
    BROLL_DIR.mkdir(exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", query.lower()).strip("_")[:50]
    cached = BROLL_DIR / f"{slug}.mp4"
    if cached.exists():
        return cached
    r = subprocess.run(
        ["yt-dlp", f"ytsearch6:{query}", "--flat-playlist",
         "--print", "%(id)s|%(duration)s|%(title).60s"],
        capture_output=True, text=True, timeout=120)
    results = []
    for line in r.stdout.strip().splitlines():
        vid, d, _title = (line.split("|", 2) + ["", ""])[:3]
        try:
            results.append((vid, float(d)))
        except ValueError:
            continue
    # dedicated scene/meme clips (10-90s) are on-topic anywhere; long videos
    # give a blind window — last resort
    ordered = [r for r in results if 10 <= r[1] <= 90] + \
              [r for r in results if not 10 <= r[1] <= 90 and 8 <= r[1] <= 480]
    tmp = BROLL_DIR / f"_{slug}_dl.mp4"
    for vid, ds in ordered[:4]:
        start = max(ds * 0.25, 1.0) if ds > 30 else max((ds - BROLL_MAX_S) / 2, 0)
        tmp.unlink(missing_ok=True)
        subprocess.run(
            ["yt-dlp", f"https://www.youtube.com/watch?v={vid}",
             "-f", "b[height<=720]/bv*[height<=720]+ba/b",
             "--download-sections", f"*{start:.0f}-{start + BROLL_MAX_S + 2:.0f}",
             "--force-overwrites", "--merge-output-format", "mp4",
             "-o", str(tmp)], capture_output=True, text=True, timeout=240)
        if not tmp.exists():
            continue
        # strip baked letterbox bars (common in fan uploads)
        cd = subprocess.run(
            ["ffmpeg", "-i", str(tmp), "-vf", "cropdetect=24:2:0",
             "-frames:v", "60", "-f", "null", "-"],
            capture_output=True, text=True)
        crops = re.findall(r"crop=(\d+:\d+:\d+:\d+)", cd.stderr)
        vf = []
        if crops:
            best = max(set(crops), key=crops.count)
            w_, h_ = map(int, best.split(":")[:2])
            if w_ > 100 and h_ > 100:
                vf.append(f"crop={best}")
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(tmp), "-t", f"{BROLL_MAX_S}", "-r", "30",
             *(["-vf", ",".join(vf)] if vf else []),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-an",
             str(cached)], capture_output=True)
        tmp.unlink(missing_ok=True)
        if cached.exists() and _broll_clean(cached):
            return cached
        cached.unlink(missing_ok=True)
    print(f"  ⚠ no clean b-roll found for “{query}”")
    return None


def _broll_clean(snippet: Path) -> bool:
    """Vision gate: reject end-cards, subscribe overlays, watermark walls."""
    ok = True
    for frac, tag in ((0.3, "a"), (0.85, "b")):
        frame = snippet.with_suffix(f".chk{tag}.png")
        subprocess.run(["ffmpeg", "-y", "-v", "error",
                        "-ss", f"{BROLL_MAX_S * frac:.1f}", "-i", str(snippet),
                        "-frames:v", "1", str(frame)], capture_output=True)
        raw = _claude_json_obj(
            f"Read the image {frame} — a candidate b-roll cutaway frame. Is it "
            "clean scene footage (movie/anime/show/sports moment)? Reject if it "
            "shows: an editor end-card ('thanks for watching'), subscribe/like "
            "overlays, channel branding walls, text slides, menus, or mostly "
            'black. STRICT JSON only: {"ok": true|false}', tools="Read")
        frame.unlink(missing_ok=True)
        if not raw.get("ok"):
            ok = False
            break
    return ok


# ── timeline ─────────────────────────────────────────────────────────────────

def _deadair_gaps(cwords: list[dict], dur: float) -> list[tuple[float, float]]:
    gaps, prev = [], 0.0
    for w in cwords:
        if w["start"] - prev > DEADAIR_GAP_S + 2 * DEADAIR_PAD_S:
            gaps.append((prev + DEADAIR_PAD_S, w["start"] - DEADAIR_PAD_S))
        prev = max(prev, w["end"])
    if dur - prev > 1.6:
        gaps.append((prev + 0.6, dur - 0.25))
    return gaps


def _at(changes: list[tuple[float, object]], t: float, default):
    """Value of the LATEST change at or before t."""
    past = [(tt, v) for tt, v in changes if tt <= t]
    return max(past, key=lambda p: p[0])[1] if past else default


def build_timeline(dur: float, edl: dict, cwords: list[dict]) -> list[dict]:
    gaps = _deadair_gaps(cwords, dur) if edl["deadair"] else []
    zoom_at: list[tuple[float, float]] = [(0.0, 1.0)]
    frame_at: list[tuple[float, str]] = [(0.0, edl["start_frame"])]
    slow: list[tuple[float, float]] = []
    push: list[tuple[float, float]] = []
    freezes: list[dict] = []
    for e in edl["events"]:
        if e["type"] == "cut":
            if e["frame"]:
                frame_at.append((e["t"], e["frame"]))
                zoom_at.append((e["t"], 1.0))
        elif e["type"] == "punch":
            zoom_at.append((e["t"], e["zoom"]))
            if e["frame"]:
                frame_at.append((e["t"], e["frame"]))
        elif e["type"] == "pulse":
            base = _at(zoom_at, e["t"], 1.0)
            zoom_at.append((e["t"], e["zoom"]))
            zoom_at.append((min(e["t"] + PULSE_S, dur), base))
        elif e["type"] == "push":
            push.append((e["t"], min(e["t"] + e["dur"], dur - 0.1)))
        elif e["type"] == "slowmo":
            slow.append((e["t"], min(e["t"] + e["dur"], dur - 0.2)))
        elif e["type"] == "freeze":
            freezes.append(e)

    bounds = {0.0, dur}
    bounds.update(t for t, _ in zoom_at)
    bounds.update(t for t, _ in frame_at)
    for s, e in slow + push + gaps:
        bounds.update((s, e))
    bounds.update(f["t"] for f in freezes)
    pts = sorted(b for b in bounds if 0.0 <= b <= dur)

    def _in(ranges, t):
        return any(s <= t < e for s, e in ranges)

    segs: list[dict] = []
    for a, b in zip(pts, pts[1:]):
        if b - a < 0.05:
            continue
        mid = (a + b) / 2
        for f in freezes:
            if abs(f["t"] - a) < 0.03:
                segs.append({"kind": "freeze", "s": a, "dur": f["dur"],
                             "zoom": max(_at(zoom_at, a, 1.0), 1.12),
                             "frame": _at(frame_at, a, edl["start_frame"]),
                             "sfx": f["sfx"]})
        if _in(gaps, mid):
            continue
        seg = {"kind": "src", "s": a, "e": b,
               "zoom": _at(zoom_at, a + 0.01, 1.0),
               "frame": _at(frame_at, a + 0.01, edl["start_frame"]),
               "speed": 0.5 if _in(slow, mid) else 1.0,
               "push": _in(push, mid)}
        prev = segs[-1] if segs else None
        if (prev and prev["kind"] == "src" and prev["e"] == a
                and all(prev[k] == seg[k] for k in ("zoom", "frame", "speed", "push"))):
            prev["e"] = b
        else:
            segs.append(seg)
    return segs[:MAX_SEGMENTS]


def _out_mapping(segs: list[dict]):
    marks, out = [], 0.0
    for g in segs:
        if g["kind"] == "src":
            marks.append((g["s"], g["e"], out, g["speed"]))
            out += (g["e"] - g["s"]) / g["speed"]
        else:
            out += g["dur"]

    def fn(t: float) -> float | None:
        for s, e, o, sp in marks:
            if s <= t < e:
                return o + (t - s) / sp
            if t < s:
                return o
        return None
    return fn, out


# ── captions — one static TikTok-native context line ─────────────────────────

_RED = r"\c&H3C3CFF&"
_WHITE = r"\c&HFFFFFF&"


def _build_phrases(cwords: list[dict], map_fn, emphasis: set,
                   max_words: int = 4, gap: float = 0.7) -> list[tuple]:
    """Word list → [(out_start, out_end, [(WORD, is_emph), ...])]."""
    groups, cur = [], []
    for w in cwords:
        if cur and (len(cur) >= max_words
                    or w["start"] - cur[-1]["end"] > gap):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    out = []
    for g in groups:
        s, e = map_fn(g[0]["start"]), map_fn(g[-1]["end"])
        if s is None:
            continue
        if e is None or e <= s:
            e = s + 1.2
        words = [(w["word"].upper(),
                  w["word"].lower().strip(".,!?\"'") in emphasis) for w in g]
        out.append((s, e, words))
    return out


def _write_ass(edl: dict, out_dur: float, out: Path, cap_y: int = 1190,
               phrases: list[tuple] | None = None) -> bool:
    if phrases:   # hard-motivation: centered all-caps phrase captions
        events = []
        for i, (s, e, words) in enumerate(phrases):
            end = min(e + 0.15, phrases[i + 1][0] if i + 1 < len(phrases)
                      else out_dur)
            # wrap: >14 chars of text per line clips at fs86 — break to 2 lines
            lines, cur, cur_len = [], [], 0
            for w, em in words:
                if cur and cur_len + len(w) > 14:
                    lines.append(cur)
                    cur, cur_len = [], 0
                cur.append((w, em))
                cur_len += len(w) + 1
            lines.append(cur)
            text = r"\N".join(
                " ".join(("{" + _RED + "}" + w + "{" + _WHITE + "}")
                         if em else w for w, em in ln) for ln in lines)
            events.append(f"Dialogue: 0,{_ass_ts(s)},{_ass_ts(end)},"
                          f"Phrase,,0,0,0,,{text}")
        out.write_text(
            "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\n"
            "PlayResY: 1920\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Phrase,Helvetica Neue,86,&H00FFFFFF,&H00FFFFFF,"
            "&H00000000,&H90000000,1,0,0,0,100,100,1,0,1,3,2,5,60,60,0,1\n\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
            "MarginV, Effect, Text\n" + "\n".join(events) + "\n")
        return bool(events)
    text = edl["caption"].strip()
    if not text:
        return False
    # wrap to ≤26 chars/line like TikTok's classic caption
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > 30:
            lines.append(cur); cur = w
        else:
            cur = f"{cur} {w}".strip()
    lines.append(cur)
    body = r"\N".join(lines)
    out.write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        "Style: Ctx,Helvetica Neue,58,&H00FFFFFF,&H00FFFFFF,&H00000000,"
        "&H73000000,1,0,0,0,100,100,0.5,0,4,9,0,5,70,70,0,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
        f"Dialogue: 1,0:00:00.00,{_ass_ts(out_dur)},Ctx,,0,0,0,,"
        r"{\pos(540," + str(cap_y) + r")\fad(200,250)}" + body + "\n")
    return True


# ── render ───────────────────────────────────────────────────────────────────

_AF = "aformat=sample_rates=44100:channel_layouts=stereo"


STACK_FACE_H = 864           # face pane height in the stacked layout
STACK_ACT_H = 1920 - STACK_FACE_H
MIN_FILL_FRAC = 0.5          # facecam must be ≥ this frame-height frac to fill 9:16


def _crop_rect(box: list | None, W: int, H: int, zoom: float,
               aspect: float = 9 / 16, inside: bool = False
               ) -> tuple[int, int, int, int]:
    """Window of `aspect` (w/h) on `box` (fractions), zoomed, clamped.
    inside=True keeps the window INSIDE the box (facecams — no neighbor
    pixels bleeding in). None box → generous center window."""
    if box:
        bx, by, bw, bh = box[0] * W, box[1] * H, box[2] * W, box[3] * H
    else:
        bx, by, bw, bh = W * 0.2, 0, W * 0.6, H
    if inside and bh < H * 0.7:   # small cams: vision boxes run a few % loose
        bx, by = bx + bw * 0.06, by + bh * 0.1
        bw, bh = bw * 0.88, bh * 0.82
    cx, cy = bx + bw / 2, by + bh / 2
    if inside:
        h = min(bh, bw / aspect)
    else:
        h = min(max(bh * 1.2, bw / aspect * 0.9), H)
    w = h * aspect
    if w > W:
        w, h = W, W / aspect
    w, h = w / zoom, h / zoom
    if inside:
        x = min(max(cx - w / 2, bx), max(bx + bw - w, bx))
        y = min(max(cy - h / 2, by), max(by + bh - h, by))
    else:
        x = min(max(cx - w / 2, 0), W - w)
        y = min(max(cy - h / 2, 0), H - h)
    ev = lambda v: int(v / 2) * 2
    return ev(w), ev(h), ev(max(x, 0)), ev(max(y, 0))


PIP_MAX_H = 360              # facecam bubble max height (study: cam ≈8-18% of frame)
PIP_X, PIP_Y = 36, 1800      # bottom-left corner; clear of the caption zone (~62%)
FACE_FILL_ENABLED = False    # 2026-07-08: face zoom-ins OFF per Taran — framing
                             # stays on the wide action crop (+ PiP bubble)


def _pip_ok(layout: dict, W: int) -> bool:
    """Small facecam + action region → game-clip layout: full-frame action
    with the whole cam as a bubble overlay (the ron1 6M-view pattern)."""
    face = layout.get("face")
    return bool(face and layout.get("action")
                and face[2] * W >= MIN_FACE_W_PX
                and face[3] < MIN_FILL_FRAC)


def _seg_vchain(src_label: str, k: int, g: dict, layout: dict,
                W: int, H: int, relax: float = 1.0) -> str:
    """Video chain for one src/freeze segment: trim → framing → effects.
    relax > 1 widens every window (self-review 'too zoomed' correction).
    Framing: 'action' = full-frame content crop (+ whole facecam as a small
    PiP bubble when one exists); 'face' = the cam fills 9:16 (reaction beat)."""
    zoom = max(g.get("zoom", 1.0) / relax, 1.0)
    frame = g.get("frame") or "action"

    if g["kind"] == "freeze":
        head = (f"{src_label}trim=start={g['s']:.3f}:end={g['s'] + 0.06:.3f},"
                f"setpts=PTS-STARTPTS,select='eq(n,0)',"
                f"tpad=stop_mode=clone:stop_duration={g['dur']:.2f}")
    else:
        head = (f"{src_label}trim=start={g['s']:.3f}:end={g['e']:.3f},"
                f"setpts=(PTS-STARTPTS)/{g['speed']}")

    face = layout.get("face")
    if (FACE_FILL_ENABLED and frame == "face" and face
            and face[2] * W >= MIN_FACE_W_PX):
        # reaction beat: cam fills the screen at its full height
        w, h, x, y = _crop_rect(face, W, H, zoom, inside=True)
        chain = head + f",crop={w}:{h}:{x}:{y},scale=1080:1920,setsar=1"
    elif _pip_ok(layout, W):
        aw, ah, ax, ay = _crop_rect(layout.get("action"), W, H, zoom)
        fw, fh, fx, fy = _crop_rect(face, W, H, 1.0,
                                    aspect=face[2] * W / (face[3] * H),
                                    inside=True)
        ph = min(PIP_MAX_H, int(fh))
        pw = max(int(fw / fh * ph / 2) * 2, 2)
        chain = (head + f",split=2[m{k}a][m{k}b];"
                 f"[m{k}a]crop={aw}:{ah}:{ax}:{ay},scale=1080:1920[m{k}s];"
                 f"[m{k}b]crop={fw}:{fh}:{fx}:{fy},scale={pw}:{ph}[m{k}c];"
                 f"[m{k}s][m{k}c]overlay={PIP_X}:{PIP_Y - ph},setsar=1")
    else:
        w, h, x, y = _crop_rect(layout.get("action"), W, H, zoom)
        chain = head + f",crop={w}:{h}:{x}:{y},scale=1080:1920,setsar=1"

    if g.get("push") and g["kind"] != "freeze":
        chain += (",zoompan=z='min(1.0+0.0005*on,1.08)':"
                  "x='(iw-iw/zoom)/2':y='(ih-ih/zoom)*0.42':"
                  "d=1:s=1080x1920:fps=30,setsar=1")
    return chain


def render(work: Path, segs, edl, sfx_cues, ass_path, wm_path, broll_path,
           layout: dict, out_dur: float, payoff_out: float, final: Path,
           relax: float = 1.0, broll_len: float = 0.0) -> None:
    W, H = _src_dims(work)
    inputs = [str(work)]
    def _add(p) -> int:
        inputs.append(str(p)); return len(inputs) - 1
    wm_i = _add(wm_path) if wm_path else None
    br_i = _add(broll_path) if broll_path else None
    music_p = MUSIC_DIR / f"{edl['music']}.mp3"
    mu_i = _add(music_p) if edl["music"] != "none" and music_p.exists() else None
    sfx_ins = [(_add(p), o, vol, trim) for p, o, vol, trim in sfx_cues]

    n_v = sum(1 for g in segs if g["kind"] in ("src", "freeze"))
    fc = f"[0:v]split={n_v}" + "".join(f"[i{i}]" for i in range(n_v)) + ";"
    pairs, vi = [], 0
    for k, g in enumerate(segs):
        fc += _seg_vchain(f"[i{vi}]", k, g, layout, W, H, relax) + f"[v{k}];"
        vi += 1
        if g["kind"] == "src":
            fc += (f"[0:a]atrim=start={g['s']:.3f}:end={g['e']:.3f},"
                   f"asetpts=PTS-STARTPTS,"
                   + (f"atempo={g['speed']},volume=0.7," if g["speed"] != 1.0 else "")
                   + f"{_AF}[a{k}];")
        else:
            fc += f"aevalsrc=0:d={g['dur']:.2f},{_AF}[a{k}];"
        pairs.append(f"[v{k}][a{k}]")
    fc += "".join(pairs) + f"concat=n={len(segs)}:v=1:a=1[catv][cata];"

    # grade: subtle (top clips are barely graded); payoff shift per template.
    # The flip is a FINAL-beat device (lacy holds it ~last third) and it RAMPS
    # in over ~0.5s — hard mid-clip flips read broken.
    pg = edl.get("payoff_grade")
    if pg != "bw_full" and broll_path:
        pg = None    # payoff-only grades don't fight a color cutaway
    grade_t = max(payoff_out, out_dur * 0.62)
    if pg == "bw_full":
        # goggins-edit genre: whole clip crushed B&W + grain; broll (overlaid
        # later) inherits the look because the grade runs AFTER the overlay
        fc += "[catv]null[graded];"
    elif pg == "dark":
        fc += ("[catv]eq="
               f"saturation='if(lt(t,{grade_t:.2f}),1.06,"
               f"max(0.45,1.06-(t-{grade_t:.2f})*1.4))':"
               f"contrast='if(lt(t,{grade_t:.2f}),1.03,"
               f"min(1.12,1.03+(t-{grade_t:.2f})*0.2))':eval=frame,"
               f"vignette=PI/4:enable='gte(t,{grade_t:.2f})'[graded];")
    elif pg == "bw":   # lacy's color→B&W payoff flip (zoom substitute)
        fc += ("[catv]eq=contrast=1.03:saturation=1.06,"
               f"hue=s='max(0,if(lt(t,{grade_t:.2f}),1,"
               f"1-(t-{grade_t:.2f})*2.2))'[graded];")
    else:
        fc += "[catv]eq=contrast=1.03:saturation=1.08[graded];"
    vin = "[graded]"

    # b-roll = alpha-dissolve overlay on the UNBROKEN timeline (L-cut: the
    # speaker's audio keeps playing under the cutaway — the #1 smoothness fix)
    if br_i is not None and broll_len > 0.3:
        t1 = payoff_out
        fc += (f"[{br_i}:v]trim=start=0:end={broll_len:.2f},"
               f"setpts=PTS-STARTPTS,scale=1080:1920:"
               f"force_original_aspect_ratio=increase,crop=1080:1920,"
               f"setsar=1,fps=30,format=yuva420p,"
               f"fade=t=in:st=0:d=0.3:alpha=1,"
               f"fade=t=out:st={max(broll_len - 0.35, 0):.2f}:d=0.35:alpha=1,"
               f"setpts=PTS+{t1:.3f}/TB[brv];"
               f"{vin}[brv]overlay=eof_action=pass[vbr];")
        vin = "[vbr]"
    if pg == "bw_full":
        fc += (f"{vin}hue=s=0,eq=contrast=1.24:brightness=-0.05,"
               f"vignette=PI/3.9,noise=alls=6:allf=t[bwv];")
        vin = "[bwv]"
    if wm_i is not None:
        fc += f"[{wm_i}:v]scale=430:-1[wmsc];{vin}[wmsc]overlay=(W-w)/2:260[wmk];"
        vin = "[wmk]"

    if mu_i is not None:
        fc += "[cata]asplit[main][sc];"
        mixes = ["[main]"]
        lo, hi = (0.14, 0.42) if edl["style"] == "aura" else (0.1, 0.2)
        fc += (f"[{mu_i}:a]atrim=0:{out_dur:.2f},{_AF},"
               f"volume='if(gte(t,{payoff_out:.2f}),{hi},{lo})':eval=frame,"
               f"afade=t=out:st={out_dur - 1.2:.2f}:d=1.2[mus];"
               f"[mus][sc]sidechaincompress=threshold=0.06:ratio=8:"
               f"attack=20:release=500[duck];")
        mixes.append("[duck]")
    else:
        fc += "[cata]anull[main];"
        mixes = ["[main]"]
    for k, (ii, cue_out, vol, trim) in enumerate(sfx_ins):
        d = max(int(cue_out * 1000), 0)
        tr = f"atrim={trim[0]}:{trim[0] + trim[1]},asetpts=PTS-STARTPTS," if trim else ""
        fc += f"[{ii}:a]{tr}{_AF},volume={vol},adelay={d}|{d}[sx{k}];"
        mixes.append(f"[sx{k}]")
    fc += ("".join(mixes) +
           f"amix=inputs={len(mixes)}:normalize=0:duration=first,"
           f"alimiter=limit=0.95[aout]")

    if ass_path:
        fc += f";{vin}subtitles=f={ass_path}:fontsdir={FONTS_DIR}[vout]"
        vin = "[vout]"

    cmd = [_ffmpeg_with_libass(), "-y"]
    for p in inputs:
        cmd += ["-i", p]
    cmd += ["-filter_complex", fc, "-map", vin, "-map", "[aout]",
            "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
            "-t", f"{out_dur:.2f}", str(final)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"ffmpeg failed:\n{r.stderr[-1800:]}")


# ── self-review: watch the render, compare to the study, correct once ───────

REVIEW_PROMPT = """You are reviewing an AUTO-EDITED streamer TikTok clip before it \
ships. Read these images:
1. {grid} — contact sheet, frames left-to-right/top-to-bottom across the clip
2. {f1} — full-resolution frame at ~25%
3. {f2} — full-resolution frame at the payoff moment

EDIT FACTS (judge the EDIT, not the game content — in-game scene changes are the
GAME's, not the editor's): {n_events} edit events over {dur:.0f}s ({eps:.2f}/s);
caption burned at ~62% height: "{caption}"; facecam PiP bubble expected
bottom-left on wide frames: {pip}.

Benchmarks from 1M-130M-view clips: faces ≈30-55% of frame height on reaction
shots (never >70%); ≤0.2 edit-cuts/sec for game clips, 0-1 total for emotional
clips; ONE readable static caption clear of faces and the PiP; no black bars or
desktop pixels bleeding at crop edges; payoff visually distinct.

Check specifically: Is the caption readable against its background in the
full-res frames? Does the caption or PiP overlap a face? Is the PiP visible and
clean (whole cam, no neighbor pixels)? Do reaction shots frame the face well?

Respond STRICT JSON only:
{{"score": <1-10>, "issues": [<zero or more of: "too_zoomed", "caption_on_face", \
"caption_unreadable", "cut_spam", "edge_bleed", "too_static", "artifact">], \
"notes": "<one sentence>"}}"""


def _review(final: Path, edl: dict, out_dur: float, payoff_out: float,
            pip: bool) -> dict:
    grid = final.with_suffix(".review.png")
    f1 = final.with_suffix(".rf1.png")
    f2 = final.with_suffix(".rf2.png")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(final),
         "-vf", f"fps={min(15 / max(out_dur, 1), 1):.3f},scale=340:-2,tile=4x4",
         "-frames:v", "1", str(grid)], check=True, capture_output=True)
    for t, p in ((out_dur * 0.25, f1), (min(payoff_out + 0.3, out_dur - 0.5), f2)):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}",
                        "-i", str(final), "-frames:v", "1", str(p)],
                       check=True, capture_output=True)
    raw = _claude_json_obj(REVIEW_PROMPT.format(
        grid=grid, f1=f1, f2=f2, dur=out_dur, caption=edl["caption"],
        n_events=len(edl["events"]), eps=len(edl["events"]) / max(out_dur, 1),
        pip="yes" if pip else "no"), tools="Read")
    for p in (f1, f2):
        p.unlink(missing_ok=True)
    if not raw or "score" not in raw:
        return {"score": None, "issues": [], "notes": "review unavailable"}
    return {"score": float(raw["score"]),
            "issues": [i for i in raw.get("issues", []) if isinstance(i, str)],
            "notes": str(raw.get("notes", ""))[:200]}


# ── entry ────────────────────────────────────────────────────────────────────

def enhance(creator: str, video_id: str, idx: int,
            style: str | None = None, broll: str | None = None) -> Path:
    vdir = _video_dir(creator, video_id)
    candidates = json.loads((vdir / "candidates.json").read_text())
    if not 1 <= idx <= len(candidates):
        raise SystemExit(f"candidate {idx} out of range (1-{len(candidates)})")
    c = candidates[idx - 1]
    creator_cfg = _load_creators().get(creator, {})
    t0 = max(c["start"] - 0.5, 0.0)
    end = c["end"]
    if creator_cfg.get("niche") == "motivation":
        # never cut a speech mid-thought — run to the next real pause
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
        # face zooms are OFF — use the person's box as a wide subject-centered
        # action crop instead (IRL streams)
        layout = {**layout, "action": layout["face"]}
    cwords = _clip_words(get_words(vdir), t0, dur)
    spikes, lulls = _energy(work, dur)
    print(f"🎛 '{c['hook']}' {dur:.0f}s — layout face={layout.get('face')} "
          f"action={layout.get('action')}")
    print(f"   spikes {spikes}, lulls {lulls}")
    edl = get_edl(creator, c, cwords, dur, spikes, lulls, layout, style)
    if edl.get("template") == "hard_motivation" and layout.get("action"):
        # speech edits never use a cam bubble — the speaker IS the frame
        layout = {**layout, "face": None}
    print(f"🎨 [{edl.get('template', edl['style'])}] “{edl['caption']}” "
          f"payoff={edl['payoff_t']:.1f}s music={edl['music']} "
          f"deadair={edl['deadair']} start={edl['start_frame']}")
    for e in edl["events"]:
        print(f"   {e['t']:5.1f}s {e['type']:6s}"
              + (f" →{e['frame']}" if e["frame"] else "")
              + (f" z{e['zoom']:.2f}" if e["type"] in ("punch", "pulse") else "")
              + (f" {e['dur']:.1f}s" if e["type"] in ("push", "slowmo", "freeze") else "")
              + (f" +{e['sfx']}" if e["sfx"] != "none" else ""))

    broll_p = Path(broll).expanduser() if broll else None
    if not broll_p and edl.get("broll_query"):
        print(f"🎞 fetching b-roll from YouTube: “{edl['broll_query']}”…")
        broll_p = fetch_broll(edl["broll_query"])
        if broll_p and edl["music"] == "none" and _music_menu():
            edl["music"] = _music_menu()[0]    # cutaway must not be silent
    segs = build_timeline(dur, edl, cwords)
    map_fn, out_dur = _out_mapping(segs)
    payoff_out = map_fn(edl["payoff_t"]) or out_dur * 0.7
    broll_dur = 0.0
    if broll_p and broll_p.exists():
        pr = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                             "format=duration", "-of", "csv=p=0", str(broll_p)],
                            capture_output=True, text=True)
        broll_dur = max(min(float(pr.stdout.strip() or 0), BROLL_MAX_S,
                            out_dur - payoff_out - 0.6), 0.0)
    cut_s = dur - sum(g["e"] - g["s"] for g in segs if g["kind"] == "src")
    print(f"🧵 {len(segs)} segments → {out_dur:.1f}s ({cut_s:.1f}s trimmed)")

    def _collect_sfx(map_fn, segs):
        cues = []
        for e in edl["events"] + [g for g in segs if g["kind"] == "freeze"]:
            name = e.get("sfx", "none")
            if name == "none":
                continue
            p, o = _sfx_path(name), map_fn(e.get("t", e.get("s", 0)))
            if p and o is not None:
                _, vol, trim = SFX.get(name, (None, 0.8, None))
                if name == "riser":
                    o = max(o - (trim[1] if trim else 2.0), 0.0)
                if not any(abs(o - po) < 0.05 for _, po, _, _ in cues):
                    cues.append((p, o, vol, trim))
        return cues

    sfx_cues = _collect_sfx(map_fn, segs)
    if broll_dur:      # soft whoosh marks the dissolve into the cutaway
        wp = _sfx_path("whoosh")
        if wp:
            sfx_cues.append((wp, max(payoff_out - 0.15, 0), 0.45, None))
    ass_path = vdir / f"clip_{idx}x.ass"
    phrases = (_build_phrases(cwords, map_fn, edl["emphasis"])
               if edl.get("caption_mode") == "phrases" else None)
    has_caps = _write_ass(edl, out_dur, ass_path, phrases=phrases)
    wm = creator_cfg.get("watermark", "")
    wm_path = (BASE / wm) if wm and (BASE / wm).exists() else None

    out_dir = OUT_ROOT / creator
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / f"{video_id}_c{idx}x.mp4"
    print(f"🎬 Rendering {_s_to_ts(t0)} → {out_dur:.0f}s, {len(sfx_cues)} sfx"
          f"{', music' if edl['music'] != 'none' else ''}"
          f"{', broll' if broll_dur else ''}{', wm' if wm_path else ''}…")
    render(work, segs, edl, sfx_cues, ass_path if has_caps else None,
           wm_path, broll_p if broll_dur else None, layout,
           out_dur, payoff_out, final, broll_len=broll_dur)

    # watch the result, compare to the study benchmarks, correct ONCE
    pip = _pip_ok(layout, _src_dims(work)[0])
    review = _review(final, edl, out_dur, payoff_out, pip)
    print(f"👁 review: {review['score']}/10 {review['issues']} — {review['notes']}")
    fixable = set(review["issues"]) & ({"too_zoomed", "caption_on_face",
                                        "cut_spam", "edge_bleed"}
                                       | ({"artifact"} if broll_dur else set()))
    if review["score"] is not None and review["score"] < 7 and fixable:
        import shutil
        keep = final.with_suffix(".v1.mp4")
        shutil.copyfile(final, keep)
        relax = 1.25 if "too_zoomed" in fixable else \
                (0.88 if "edge_bleed" in fixable else 1.0)
        cap_y = 1420 if "caption_on_face" in fixable else 1190
        if "artifact" in fixable and broll_dur:
            print("   dropping the b-roll cutaway (artifact flagged)")
            broll_dur, broll_p = 0.0, None
        if "cut_spam" in fixable:
            edl["events"] = [e for e in edl["events"]
                             if e["type"] not in ("punch", "pulse")]
            segs = build_timeline(dur, edl, cwords)
            map_fn, out_dur = _out_mapping(segs)
            payoff_out = map_fn(edl["payoff_t"]) or out_dur * 0.7
            sfx_cues = _collect_sfx(map_fn, segs)
        has_caps = _write_ass(edl, out_dur, ass_path, cap_y, phrases=phrases)
        print(f"🔁 correcting ({', '.join(sorted(fixable))}) and re-rendering…")
        render(work, segs, edl, sfx_cues, ass_path if has_caps else None,
               wm_path, broll_p if broll_dur else None, layout,
               out_dur, payoff_out, final, relax=relax, broll_len=broll_dur)
        review2 = _review(final, edl, out_dur, payoff_out, pip)
        print(f"👁 re-review: {review2['score']}/10 {review2['issues']} — "
              f"{review2['notes']}")
        if review2["score"] is not None and review["score"] is not None \
                and review2["score"] < review["score"]:
            shutil.move(keep, final)         # correction regressed — keep v1
            print("↩ correction scored lower — kept the first render")
        else:
            keep.unlink(missing_ok=True)
            review = review2
    final.with_suffix(".review.json").write_text(json.dumps(review))
    print(f"✅ {final}  ({final.stat().st_size / 1e6:.1f} MB)  "
          f"[{edl['style']}] {edl['caption']}")
    try:
        from tools import clip_sheet
        clip_sheet.append(creator, c["hook"], final.stem,
                          notes=f"{out_dur:.0f}s · {edl.get('template', '?')} template")
    except Exception as e:  # noqa: BLE001
        print(f"⚠ sheet tracking skipped: {e}")
    return final


def cmd_enhance(args):
    style = broll = None
    legacy = {"meme": "game_bubble", "aura": "quote_aura"}
    for a in args[3:]:
        if a in TEMPLATES:
            style = a
        elif a in legacy:
            style = legacy[a]
        elif a.startswith("broll="):
            broll = a.split("=", 1)[1]
    enhance(args[0], args[1], int(args[2]), style, broll)


if __name__ == "__main__":
    if len(sys.argv) < 5 or sys.argv[1] != "enhance":
        print(__doc__)
        sys.exit(1)
    cmd_enhance(sys.argv[2:])
