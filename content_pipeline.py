#!/usr/bin/env python3
"""
Short-form content pipeline.
Usage:
    python3 content_pipeline.py script.txt

Script format (script.txt):
    Each line = one sentence/segment.
    Optionally add a keyword hint after | for B-roll search:

    Example:
        Most people never question why they do what they do. | crowd walking
        They just follow the path laid out for them. | empty road
        School. Job. Retire. | calendar desk clock

Output: ~/Desktop/AITA Renders/<script_title>.mp4  (1080x1920, TikTok/Reels ready)
        Captions are burned in automatically via Captacity. Set `captions: false`
        in the header block to skip and add captions manually in CapCut instead.

Requirements:
    pip install elevenlabs requests captacity "moviepy<2"
    ELEVENLABS_API_KEY and PEXELS_API_KEY in environment or .env file
"""

import os
import sys
import json
import random
import subprocess
import tempfile
from pathlib import Path

import re
import hashlib
import shutil
import requests

# ── Config ─────────────────────────────────────────────────────────────────────
FFMPEG         = "/opt/homebrew/bin/ffmpeg"
FFPROBE        = "/opt/homebrew/bin/ffprobe"
OUT_W, OUT_H   = 1080, 1920
FRAME_MODE     = "full"   # "full" = footage fills canvas | "ytbox" = 16:9 strip boxed in fake YouTube chrome
FONT_PATH      = "/System/Library/Fonts/Helvetica.ttc"
VOICE_ID       = "pNInz6obpgDQGcFmaJgB"  # ElevenLabs "Adam" — deep American male, AITA-tier default
VOICE_SPEED    = 1.0                       # storytime cadence — faster than introspective

# Voice profiles selectable per-script via `voice:` header field.
# Each profile: (voice_id, speed, stability, similarity_boost)
VOICE_PROFILES = {
    "adam":          ("pNInz6obpgDQGcFmaJgB", 1.0,  0.72, 0.75),  # AITA storytime default
    "will_growth":   ("bIHbv24MWmeRgasZH58o", 0.95, 0.60, 0.75),  # relaxed young male — Growth/journal niche
    "chris_growth":  ("iP95p4xoKVk53GoZ742B", 0.95, 0.60, 0.75),  # casual down-to-earth — Growth niche pick
    "bill":          ("pqHfZKP75CvOlQylNhV4", 0.92, 0.80, 0.78),  # gravelly elder — Stoic / motivational
    "daniel":        ("onwK4e9ZLuTAKqWW03F9", 0.95, 0.78, 0.75),  # deep British narrator
    "drew":          ("29vD33N1CtxCmqQRPOHJ", 1.0,  0.72, 0.75),  # deep American narrative
    "drew_horror":   ("29vD33N1CtxCmqQRPOHJ", 0.95, 0.65, 0.80),  # tuned for TikTok pacing (was 0.85)
    "bill_horror":   ("pqHfZKP75CvOlQylNhV4", 0.95, 0.70, 0.80),  # tuned for TikTok pacing (was 0.85)
    "callum":        ("N2lVS1w4EtoT3dr4eOWO", 1.0,  0.55, 0.80),  # husky trickster male — characters_animation
    "callum_horror": ("N2lVS1w4EtoT3dr4eOWO", 1.05, 0.50, 0.82),  # menacing horror narrator (replaces Bill)
    "brian_horror":  ("nPczCjzI2devNBz1zQrb", 1.0,  0.60, 0.80),  # deep resonant alt narrator
    "harry_horror":  ("SOYHLrjzK2X1ezoPC6cr", 1.0,  0.50, 0.80),  # fierce warrior — aggressive horror alt
    "sarah":         ("EXAVITQu4vr4xnSDxMaL", 1.0,  0.72, 0.75),  # female — soothing/maternal default
    "sarah_horror":  ("EXAVITQu4vr4xnSDxMaL", 1.0,  0.55, 0.78),  # female victim (legacy — Elli preferred)
    "elli":          ("MF3mGyEYCl7XYWbV9V6O", 1.0,  0.72, 0.75),  # NOT available on keys — kept for reference
    "elli_horror":   ("MF3mGyEYCl7XYWbV9V6O", 1.0,  0.50, 0.78),  # NOT available on keys — kept for reference
    "jessica":       ("cgSgspJ2msm6clMCkdW9", 1.0,  0.65, 0.75),  # young female — playful/bright/cute baseline
    "jessica_horror":("cgSgspJ2msm6clMCkdW9", 1.1,  0.40, 0.78),  # young female victim — faster + max fear
    "lily":          ("pFZP5JQG7iQjIQuC4Bku", 1.0,  0.70, 0.75),  # velvety actress — strong emotional range
    "lily_horror":   ("pFZP5JQG7iQjIQuC4Bku", 0.97, 0.50, 0.78),  # alt victim — more theatrical
}

# Template pools selectable per-script via `template_pool:` header field.
# Each render randomly picks (broll, music) from the matching pool.
# Pool entries are filtered against actual cached assets at runtime.
TEMPLATE_POOLS = {
    "aita": {
        "broll": ["parkour", "subway", "asmr_slime", "fortnite", "cooking", "gta_driving"],
        "music": [
            "suspenseful_storytime_lofi",
            "sad_piano_lofi_no_copyright",
            "emotional_piano_instrumental_no_copyright",
            "melancholic_piano_background",
            "dark_cinematic_piano",
            "ambient_synth_dark",
            "soft_strings_emotional",
            "tense_thriller_pads",
        ],
    },
    "stoic": {
        "broll": ["nature_waves", "nature_mountains", "nature_forest",
                  "nature_sunset", "nature_birds", "nature_aurora", "nature_wildlife"],
        "music": ["epic_cinematic_strings", "hopeful_orchestral",
                  "contemplative_piano", "ambient_meditation"],
    },
    "horror": {
        # Animated/Skibidi-style B-roll only — chosen 2026-05-21 for viral-format fit
        "broll": ["horror_animated"],
        # philmen_bg is primary (user-picked 2026-05-21); others are alts
        "music": ["philmen_bg", "horror_ambient_drone", "horror_tense_pads",
                  "horror_creepy_atmospheric", "horror_bass_swell"],
    },
}
CAPTION_SIZE   = 52
CAPTION_COLOR  = "white"
CAPTION_SHADOW = "black@0.7"
BG_MUSIC_VOL   = 0.12                      # audible but beneath the voice
OUTRO_TAIL     = 1.0                       # seconds of music-only outro after the last spoken line
MUSIC_DELAY_DEFAULT = 3.0                  # seconds before the music bed enters (override per-script via music_delay:)
# VHS ambience (ambience: static — default when caption_style is vhs).
# "Rain on window" flavor (Taran's pick 2026-07-17 from the static-flavor audition):
# lowpassed white noise with slow tremolo swells, sits ~-30dB under the mix.
# Previous flavor (smooth pink cassette hiss, shipped LOOSE03-06):
#   SRC "anoisesrc=color=pink:amplitude=0.05:seed=42"  SHAPE "highpass=f=400,lowpass=f=9000,volume=0.55"
STATIC_NOISE_SRC   = "anoisesrc=color=white:amplitude=0.05:seed=42"
STATIC_NOISE_SHAPE = "highpass=f=300,lowpass=f=6000,tremolo=f=0.3:d=0.3,volume=0.28"  # 0.55 was too loud for the rain flavor (Taran, 2026-07-18)
MUSIC_PATH     = Path.home() / "agentic_os" / "bg_music.mp3"
BROLL_SOURCE   = "youtube"    # "youtube", "pexels", or "local"
GAME_TAG       = "LittleBigPlanet"  # prepended to yt-dlp searches
BROLL_CACHE    = Path.home() / "agentic_os" / "broll_cache"  # local pre-downloaded loops
MUSIC_CACHE    = Path.home() / "agentic_os" / "music_cache"  # royalty-free track library
VOICE_CACHE    = Path.home() / "agentic_os" / "voice_cache"  # narrator takes keyed by (voice, settings, text) — re-renders with an unchanged script cost 0 ElevenLabs chars
MUSIC_QUERY    = "calm ambient piano soft background music no copyright"

ELEVENLABS_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_KEY_FALLBACK = os.environ.get("ELEVENLABS_API_KEY_FALLBACK", "")
ELEVENLABS_KEY_FALLBACK_2 = os.environ.get("ELEVENLABS_API_KEY_FALLBACK_2", "")
PEXELS_KEY     = os.environ.get("PEXELS_API_KEY", "")

# Fallback search terms — early gaming era nostalgia aesthetic (2016–2018)
DEFAULT_KEYWORDS = [
    "friends gaming together couch",
    "gaming setup rgb neon glow",
    "teenager bedroom night",
    "hands keyboard gaming",
    "neon lights dark room",
    "colorful computer screen glow",
    "late night gaming",
    "retro video game controller",
    "person gaming alone night",
    "childhood bedroom window light",
]

# ── Parse script ────────────────────────────────────────────────────────────────
def parse_script(path: Path) -> tuple[dict, list[dict]]:
    """Parse script file. Returns (meta, segments).

    Optional header block between --- markers sets per-video config:
        game, title, theme, music (all optional, fall back to module-level defaults)
    """
    text = path.read_text().strip()
    meta = {}

    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip().lower()] = v.strip()
            body = parts[2].strip()
        else:
            body = text
    else:
        body = text

    segments = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Strip optional speaker tag at line start: [N], [V], [n], [v]
        speaker = None
        m = re.match(r"^\[([NVnv])\]\s*", line)
        if m:
            speaker = m.group(1).upper()
            line = line[m.end():].strip()
            if not line:
                continue
        if "|" in line:
            text_part, keyword = line.split("|", 1)
            segments.append({"text": text_part.strip(), "keyword": keyword.strip(),
                             "speaker": speaker})
        else:
            segments.append({"text": line, "keyword": random.choice(DEFAULT_KEYWORDS),
                             "speaker": speaker})

    return meta, segments


# ── ElevenLabs voiceover ────────────────────────────────────────────────────────
def generate_voiceover(text: str, out_path: Path, voice_profile: str = "adam") -> None:
    """Generate MP3 voiceover via ElevenLabs API.

    Tries the primary key first; on 401 / 429 / quota errors, falls back to
    ELEVENLABS_API_KEY_FALLBACK if set. Useful when the primary key's monthly
    free-tier quota is exhausted. voice_profile selects from VOICE_PROFILES.
    """
    if not ELEVENLABS_KEY:
        raise RuntimeError("Set ELEVENLABS_API_KEY environment variable")

    profile = VOICE_PROFILES.get(voice_profile, VOICE_PROFILES["adam"])
    voice_id, speed, stability, similarity_boost = profile

    # Voice cache: same voice + settings + exact text → reuse the saved take.
    # Lets a tape re-render (music/footage/format changes) without re-burning quota.
    cache_key = hashlib.sha1(
        f"{voice_id}|{speed}|{stability}|{similarity_boost}|eleven_multilingual_v2|{text}".encode()
    ).hexdigest()
    cached_take = VOICE_CACHE / f"{cache_key}.mp3"
    if cached_take.exists() and cached_take.stat().st_size > 1_000:
        shutil.copyfile(cached_take, out_path)
        print("  ♻ voice cache hit (0 chars burned)")
        return

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": stability,
            "similarity_boost": similarity_boost,
            "speed": speed,
        },
    }

    # Build ordered key chain — primary first, then each fallback in turn
    candidate_keys = [("primary", ELEVENLABS_KEY)]
    for label, k in (("fallback", ELEVENLABS_KEY_FALLBACK),
                     ("fallback2", ELEVENLABS_KEY_FALLBACK_2)):
        if k and k not in {entry[1] for entry in candidate_keys}:
            candidate_keys.append((label, k))

    last_err = None
    for i, (label, key) in enumerate(candidate_keys):
        headers = {"xi-api-key": key, "Content-Type": "application/json"}
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        has_next = i < len(candidate_keys) - 1
        if r.status_code in (401, 402, 429) and has_next:
            print(f"  ⚠ ElevenLabs '{label}' returned {r.status_code} — trying next key")
            last_err = r
            continue
        r.raise_for_status()
        if label != "primary":
            print(f"  ↻ used {label} ElevenLabs key")
        out_path.write_bytes(r.content)
        VOICE_CACHE.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(out_path, cached_take)
        return

    if last_err is not None:
        last_err.raise_for_status()


# ── Pexels B-roll ───────────────────────────────────────────────────────────────
def fetch_broll(keyword: str, duration_needed: float, out_path: Path) -> None:
    """Download a Pexels video clip matching keyword, at least duration_needed seconds."""
    if not PEXELS_KEY:
        raise RuntimeError("Set PEXELS_API_KEY environment variable")

    url = "https://api.pexels.com/videos/search"
    headers = {"Authorization": PEXELS_KEY}
    params = {"query": keyword, "orientation": "portrait", "per_page": 15, "size": "medium"}

    r = requests.get(url, headers=headers, params=params, timeout=15)
    r.raise_for_status()
    videos = r.json().get("videos", [])

    # Pick shortest clip that is long enough (.get guards malformed API rows)
    candidates = [v for v in videos if (v.get("duration") or 0) >= duration_needed]
    if not candidates:
        candidates = videos  # fallback: use whatever is available
    if not candidates:
        # Empty result (no match / rate-limited) — raise a clear error instead of
        # letting random.choice([]) blow up with a cryptic IndexError.
        raise RuntimeError(f"No Pexels clips returned for '{keyword}'")

    clip = random.choice(candidates[:5])  # randomise from top 5 matches

    # Prefer HD file
    files = sorted(clip.get("video_files") or [], key=lambda f: f.get("width", 0), reverse=True)
    if not files or not files[0].get("link"):
        raise RuntimeError(f"Pexels clip for '{keyword}' has no downloadable file")
    download_url = files[0]["link"]

    data = requests.get(download_url, timeout=60).content
    out_path.write_bytes(data)


# ── Background music (royalty-free cache → fallback yt-dlp) ─────────────────────
def pick_random_template(pool: str = "aita") -> tuple[str, Path]:
    """Pick a random (broll_category, music_path) combo from the named pool.

    Filters the pool's declared B-roll + music against what's actually cached
    on disk, so missing downloads don't break the render. Falls back to all
    cached assets if the pool name is unknown.
    """
    pool_spec = TEMPLATE_POOLS.get(pool)
    if pool_spec is None:
        # Unknown pool — fall back to whatever's cached
        all_cats = [d.name for d in BROLL_CACHE.iterdir() if d.is_dir() and any(d.glob("*.mp4"))]
        all_music = list(MUSIC_CACHE.glob("*.mp3"))
        if not all_cats or not all_music:
            raise RuntimeError(f"Unknown pool '{pool}' and cache is empty")
        return random.choice(sorted(all_cats)), random.choice(sorted(all_music))

    # Intersect pool with actual cache
    valid_cats = [
        c for c in pool_spec["broll"]
        if (BROLL_CACHE / c).is_dir() and any((BROLL_CACHE / c).glob("*.mp4"))
    ]
    valid_music = [
        MUSIC_CACHE / f"{m}.mp3" for m in pool_spec["music"]
        if (MUSIC_CACHE / f"{m}.mp3").exists()
    ]
    if not valid_cats:
        raise RuntimeError(f"Pool '{pool}' has no cached B-roll categories yet")
    if not valid_music:
        raise RuntimeError(f"Pool '{pool}' has no cached music tracks yet")
    return random.choice(valid_cats), random.choice(valid_music)


def pick_cached_music(mood_query: str) -> Path | None:
    """Pick the best-matching royalty-free track from MUSIC_CACHE.

    Matches by overlapping keywords between mood_query and filenames. Returns
    None if cache is empty or no match (caller falls back to yt-dlp).
    """
    tracks = sorted(MUSIC_CACHE.glob("*.mp3"))
    if not tracks:
        return None
    query_words = {w.lower() for w in mood_query.replace("_", " ").split() if len(w) > 2}
    best_track, best_score = None, -1
    for t in tracks:
        name_words = set(t.stem.lower().replace("_", " ").split())
        score = len(query_words & name_words)
        if score > best_score:
            best_score, best_track = score, t
    # If nothing overlaps, pick at random (any cached track is safer than yt-dlp)
    return best_track if best_score > 0 else random.choice(tracks)


def _strip_leading_silence(path: Path) -> None:
    """Condition the music bed in-place: trim leading silence + normalize loudness.

    Guarantees (a) the first audible note lands exactly where the mix schedules it
    (old-piano recordings often open with a second of near-silence), and (b) every
    bed hits the mix at the same perceived level — source tracks vary wildly
    (-16 to -30 dB mean), which made quiet recordings inaudible at BG_MUSIC_VOL."""
    trimmed = path.with_suffix(".trim.mp3")
    cmd = [
        FFMPEG, "-y", "-i", str(path),
        "-af", "silenceremove=start_periods=1:start_threshold=-35dB:detection=peak,"
               "loudnorm=I=-16:TP=-1.5:LRA=11",
        str(trimmed),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode == 0 and trimmed.exists() and trimmed.stat().st_size > 10_000:
        trimmed.replace(path)
    else:
        trimmed.unlink(missing_ok=True)


def fetch_bg_music(query: str = MUSIC_QUERY) -> None:
    """Pick a track from MUSIC_CACHE if available, else fall back to yt-dlp."""
    import shutil

    cached = pick_cached_music(query)
    if cached is not None:
        import shutil as _sh
        _sh.copyfile(cached, MUSIC_PATH)
        _strip_leading_silence(MUSIC_PATH)
        print(f"  → Music: {cached.name} (royalty-free cache)")
        return

    if not shutil.which("yt-dlp"):
        print("  (yt-dlp not found and cache empty — skipping music)")
        return

    print(f"Music cache empty — fetching from YouTube: \"{query}\"…")
    cmd = [
        "yt-dlp",
        f"ytsearch5:{query}",
        "--no-playlist",
        "--match-filter", "duration > 180",
        "-x", "--audio-format", "mp3",
        "--audio-quality", "0",
        "--max-downloads", "1",
        "-o", str(MUSIC_PATH.parent / "bg_music.%(ext)s"),
        "--quiet", "--no-warnings",
    ]
    subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if MUSIC_PATH.exists():
        _strip_leading_silence(MUSIC_PATH)
        print(f"  → Music cached at {MUSIC_PATH.name}")
    else:
        print(f"  Warning: music download failed — video will have no background music")


# ── YouTube B-roll via yt-dlp ───────────────────────────────────────────────────
def fetch_broll_youtube(game: str, keyword: str, duration_needed: float, out_path: Path) -> None:
    """Download a B-roll clip from YouTube. Falls back to Pexels on any failure."""
    import shutil
    if not shutil.which("yt-dlp"):
        print("  (yt-dlp not found — falling back to Pexels)")
        fetch_broll(keyword, duration_needed, out_path)
        return

    query = f"{game} {keyword}".strip() if game else keyword

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        cmd = [
            "yt-dlp",
            f"ytsearch5:{query}",
            "--no-playlist",
            "--match-filter", "duration > 40",
            "-f", "bestvideo[height>=720][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--download-sections", "*00:00:05-00:01:15",
            "--max-downloads", "1",
            "-o", str(td / "clip.%(ext)s"),
            "--quiet", "--no-warnings",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        candidates = list(td.glob("clip.*"))
        if not candidates:
            print(f"  (yt-dlp: no results for '{query}' — falling back to Pexels)")
            fetch_broll(keyword, duration_needed, out_path)
            return

        trim_cmd = [
            FFMPEG, "-y",
            "-i", str(candidates[0]),
            "-t", str(duration_needed + 1),
            "-an",
            "-c:v", "libx264", "-crf", "22",
            "-pix_fmt", "yuv420p",
            str(out_path),
        ]
        r2 = subprocess.run(trim_cmd, capture_output=True, text=True)
        if r2.returncode != 0:
            print(f"  (yt-dlp trim failed — falling back to Pexels)")
            fetch_broll(keyword, duration_needed, out_path)


# ── Local B-roll (pre-downloaded gameplay loops) ────────────────────────────────
def fetch_broll_local(category: str, duration_needed: float, out_path: Path) -> None:
    """Grab a random slice from a pre-downloaded gameplay clip.

    Files live at ~/agentic_os/broll_cache/<category>/*.mp4. We pick a random
    file, then a random in-point inside it, and trim duration_needed+1s.
    Falls back to Pexels if cache is empty.
    """
    category_dir = BROLL_CACHE / (category or "parkour")
    clips = sorted(category_dir.glob("*.mp4"))
    if not clips:
        print(f"  (no local B-roll in {category_dir} — falling back to Pexels)")
        fetch_broll(category or "gameplay", duration_needed, out_path)
        return

    clip = random.choice(clips)
    clip_dur = get_duration(clip)
    max_start = max(0.0, clip_dur - duration_needed - 2.0)
    start = random.uniform(0.0, max_start) if max_start > 0 else 0.0

    cmd = [
        FFMPEG, "-y",
        "-ss", f"{start:.2f}",
        "-i", str(clip),
        "-t", str(duration_needed + 1),
        "-an",
    ]
    if "camR" in clip.name and FRAME_MODE != "ytbox":
        # source has a top-right facecam (e.g. PopularMMOs) — pre-crop the slice
        # vertical with a RIGHT-anchored window so the cam survives; the centered
        # crop in assemble_segment then becomes a no-op on the already-9:16 slice.
        # (ytbox shows the full 16:9 frame, so no crop is needed there at all)
        cmd += ["-vf", (f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
                        f"crop={OUT_W}:{OUT_H}:iw-ow:(ih-oh)/2,setsar=1")]
    cmd += [
        "-c:v", "libx264", "-crf", "22",
        "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  (local trim failed — falling back to Pexels)")
        fetch_broll(category or "gameplay", duration_needed, out_path)


# ── AI image B-roll via Pollinations.ai + Ken Burns zoom ────────────────────────
POLLINATIONS_BASE = "https://image.pollinations.ai/prompt/{prompt}?width=576&height=1024&nologo=true&model=flux&seed={seed}"
AI_STYLE_SUFFIX   = "cinematic dramatic lighting photorealistic 4k emotional portrait no text"

def _scene_prompt(segment_text: str, story_theme: str) -> str:
    """Build a Pollinations prompt from segment text + story theme.

    Uses the segment text as the primary visual anchor so each image matches
    what's being narrated. The story_theme adds scene/setting context.
    """
    context = f" | setting: {story_theme}" if story_theme else ""
    return f"{segment_text[:140]}{context}, {AI_STYLE_SUFFIX}"

def fetch_broll_ai_image(segment_text: str, story_theme: str, duration: float, out_path: Path) -> None:
    """Generate an AI image via Pollinations.ai and animate it with a Ken Burns zoom.

    Falls back to local B-roll (parkour) if Pollinations fails.
    """
    import urllib.parse

    prompt = _scene_prompt(segment_text, story_theme)
    seed   = random.randint(1, 99999)
    url    = POLLINATIONS_BASE.format(prompt=urllib.parse.quote(prompt), seed=seed)

    img_tmp = out_path.with_suffix(".jpg")
    try:
        r = requests.get(url, timeout=45)
        r.raise_for_status()
        if "image" not in r.headers.get("content-type", ""):
            raise RuntimeError("Non-image response from Pollinations")
        img_tmp.write_bytes(r.content)
    except Exception as e:
        print(f"  (Pollinations failed: {e} — falling back to local B-roll)")
        fetch_broll_local("parkour", duration, out_path)
        return

    # Ken Burns: start zoomed in 1.5x, slowly pull back to 1.0x
    frames = int(duration * 30) + 10
    zoom_filter = (
        f"zoompan="
        f"z='if(lte(zoom,1.0),1.5,max(1.001,zoom-0.0015))':"
        f"d={frames}:"
        f"x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':"
        f"s={OUT_W}x{OUT_H},"
        f"fps=30"
    )
    cmd = [
        FFMPEG, "-y",
        "-loop", "1",
        "-i", str(img_tmp),
        "-vf", zoom_filter,
        "-t", str(duration + 0.2),
        "-c:v", "libx264", "-crf", "22",
        "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    img_tmp.unlink(missing_ok=True)
    if r.returncode != 0:
        print(f"  (Ken Burns ffmpeg failed — falling back to local B-roll)")
        fetch_broll_local("parkour", duration, out_path)


# ── ffprobe duration ────────────────────────────────────────────────────────────
def get_duration(path: Path) -> float:
    r = subprocess.run(
        [FFPROBE, "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    # Don't float('') a failed probe — surface a clear error so a single bad
    # asset doesn't abort the whole render with an opaque ValueError.
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"ffprobe failed for {path}: {(r.stderr or '').strip()[-200:]}")
    try:
        return float(r.stdout.strip())
    except ValueError:
        raise RuntimeError(f"ffprobe non-numeric duration for {path}: {r.stdout.strip()[:80]}")


# ── Assemble one segment ────────────────────────────────────────────────────────
def assemble_segment(audio: Path, broll: Path, caption: str, out: Path,
                     tail: float = 0.0) -> None:
    """Combine B-roll + voiceover into a single segment clip. Captions added in CapCut.

    tail: extra seconds of B-roll (audio padded with silence) after the VO ends —
    used on the final segment so the music can linger."""
    dur = get_duration(audio) + tail

    if FRAME_MODE == "ytbox":
        strip_h = (OUT_W * 9 // 16) & ~1          # 16:9 strip, even height
        strip_y = (OUT_H - strip_h) // 2
        scale_filter = (
            f"scale={OUT_W}:{strip_h}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{strip_h},"
            f"pad={OUT_W}:{OUT_H}:0:{strip_y}:color=black,"
            f"setsar=1,fps=30"
        )
    else:
        scale_filter = (
            f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{OUT_H},"
            f"setsar=1,fps=30"
        )

    cmd = [
        FFMPEG, "-y",
        "-stream_loop", "-1", "-i", str(broll),
        "-i", str(audio),
        "-filter_complex", f"[0:v]{scale_filter}[v];[1:a]apad[a]",
        "-map", "[v]",
        "-map", "[a]",
        "-t", str(dur),
        "-c:v", "libx264", "-crf", "20", "-r", "30",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  ERROR assembling segment:\n{r.stderr[-2000:]}")
        raise RuntimeError("ffmpeg segment assembly failed")


# ── Color grade + music mix ─────────────────────────────────────────────────────
def apply_grade(inp: Path, out: Path, music_delay: float = 0.0, ambience: str = "none") -> None:
    """Warm nostalgic grade + optional background music mix.

    music_delay: seconds of silence before the music bed enters — used to hold
    the music until the narrator's first line has landed (then 1.2s fade-in).
    ambience: "static" layers VHS tape hiss (band-limited pink noise) under the
    whole mix — audible in pauses, sits below narration. Default for vhs style."""
    # Slightly warm, slightly vibrant — evokes early-era gaming/YouTube nostalgia
    vf = (
        "eq=saturation=0.88:contrast=1.03:brightness=-0.01,"
        "colorbalance=rs=0.04:gs=0.01:bs=-0.04:rm=0.02:gm=0:bm=-0.02:rh=0:gh=0:bh=0"
    )

    if MUSIC_PATH.exists():
        vid_dur = get_duration(inp)
        music_filters = []
        if music_delay > 0:
            music_filters.append(f"adelay={int(music_delay * 1000)}:all=1")
            music_filters.append(f"afade=t=in:st={music_delay:.2f}:d=0.25")
        music_filters.append(f"volume={BG_MUSIC_VOL}")
        cmd = [
            FFMPEG, "-y",
            "-i", str(inp),
            "-stream_loop", "-1", "-i", str(MUSIC_PATH),
        ]
        if ambience == "static":
            print("  → static ambience layered (tape hiss)")
            cmd += ["-f", "lavfi", "-i", STATIC_NOISE_SRC]
            mix = (f"[0:v]{vf}[v];"
                   f"[1:a]{','.join(music_filters)}[m];"
                   f"[2:a]{STATIC_NOISE_SHAPE}[st];"
                   f"[0:a][m][st]amix=inputs=3:duration=first:dropout_transition=2:normalize=0[a]")
        else:
            mix = (f"[0:v]{vf}[v];"
                   f"[1:a]{','.join(music_filters)}[m];"
                   f"[0:a][m]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[a]")
        cmd += [
            "-filter_complex", mix,
            "-map", "[v]",
            "-map", "[a]",
            "-t", str(vid_dur),
            "-c:v", "libx264", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            str(out),
        ]
    elif ambience == "static":
        print("  (no music bed — static ambience only)")
        vid_dur = get_duration(inp)
        cmd = [
            FFMPEG, "-y", "-i", str(inp),
            "-f", "lavfi", "-i", STATIC_NOISE_SRC,
            "-filter_complex",
                f"[0:v]{vf}[v];"
                f"[1:a]{STATIC_NOISE_SHAPE}[st];"
                f"[0:a][st]amix=inputs=2:duration=first:normalize=0[a]",
            "-map", "[v]", "-map", "[a]",
            "-t", str(vid_dur),
            "-c:v", "libx264", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            str(out),
        ]
    else:
        print("  (bg_music.mp3 not found — skipping music mix)")
        cmd = [
            FFMPEG, "-y", "-i", str(inp),
            "-vf", vf,
            "-c:v", "libx264", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            str(out),
        ]

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"Color grade/music mix failed: {r.stderr[-1000:]}")


# ── Burn-in captions via Captacity ──────────────────────────────────────────────
def burn_captions(inp: Path, out: Path, segments: list[dict], durations: list[float],
                  style: str = "default") -> None:
    """Burn word-by-word captions into the final video.

    We skip Whisper entirely by feeding Captacity pre-built segments: we already
    know the exact spoken text per line and the exact audio duration per line,
    so we distribute words evenly across each segment. Perfectly accurate, no
    transcription, no API key.
    """
    # moviepy 1.x needs ImageMagick for text rendering — point it at brew install
    os.environ.setdefault("IMAGEMAGICK_BINARY", "/opt/homebrew/bin/magick")
    import captacity

    cap_segments = []
    cursor = 0.0
    # Weight each word's caption duration by character count + a per-word constant.
    # The constant prevents 1-char words from getting near-zero time.
    # This tracks actual speech better than uniform distribution.
    WORD_CONST = 3  # ~3 char-equivalents of transition/breath time per word
    for seg, dur in zip(segments, durations):
        words = seg["text"].split()
        if not words:
            cursor += dur
            continue
        weights = [len(w) + WORD_CONST for w in words]
        total_w = sum(weights)
        word_objs = []
        local_cursor = 0.0
        for w, weight in zip(words, weights):
            slice_dur = (weight / total_w) * dur
            word_objs.append({
                "word": " " + w,
                "start": cursor + local_cursor,
                "end":   cursor + local_cursor + slice_dur,
            })
            local_cursor += slice_dur
        cap_segments.append({
            "start": cursor,
            "end":   cursor + dur,
            "words": word_objs,
        })
        cursor += dur

    style_kwargs = CAPTION_STYLES.get(style, CAPTION_STYLES["default"])
    captacity.add_captions(
        video_file=str(inp),
        output_file=str(out),
        line_count=2,
        padding=60,
        shadow_strength=1.0,
        shadow_blur=0.15,
        segments=cap_segments,
        print_info=False,
        **style_kwargs,
    )


# ── Main ────────────────────────────────────────────────────────────────────────
# Caption styles selectable per-script via `caption_style:` header field.
# Kwargs are forwarded to captacity.add_captions (font is a ttf path).
CAPTION_STYLES = {
    "default": {  # restrained word-highlight — AITA/Stoic/Horror house style
        "font_size": 90, "font_color": "white",
        "stroke_color": "black", "stroke_width": 3,
        "highlight_current_word": True, "word_highlight_color": "#F5B041",
    },
    "vhs": {  # camcorder mono — Growth niche; pairs with apply_vhs_overlay
        "font": "/System/Library/Fonts/Supplemental/Courier New Bold.ttf",
        "font_size": 60, "font_color": "white",
        "stroke_color": "black", "stroke_width": 2,
        "highlight_current_word": True, "word_highlight_color": "#F5B041",
    },
}

VHS_DEFAULT_DATE = "AUG 12 2014"

def _build_vhs_overlay_png(path: Path, date_text: str, w: int, h: int, label: str = "",
                           yt_title: str = "") -> None:
    """Transparent camcorder UI layer: ● REC, PLAY ▸ (drawn triangle), date stamp.

    Optional `label` (e.g. "TAPE 1 OF 5") stamps top-right for series branding.
    In FRAME_MODE "ytbox" the stamps anchor INSIDE the 16:9 footage strip and a
    fake YouTube player panel (title, channel row, Subscribe, like pills) is
    drawn under the strip — the vanshon-style screen-recording look."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    COURIER = "/System/Library/Fonts/Supplemental/Courier New Bold.ttf"

    if FRAME_MODE == "ytbox":
        strip_h = (w * 9 // 16) & ~1
        strip_y = (h - strip_h) // 2
        f = ImageFont.truetype(COURIER, int(strip_h * 0.052))
        top_y, bot_y = strip_y + int(strip_h * 0.05), strip_y + strip_h - int(strip_h * 0.115)
    else:
        strip_h, strip_y = h, 0
        f = ImageFont.truetype(COURIER, int(h * 0.026))
        top_y, bot_y = int(h * 0.055), h - int(h * 0.075)

    def stamped(pos, text, fill):
        x, y = pos
        d.text((x + 3, y + 3), text, font=f, fill=(0, 0, 0, 150))
        d.text((x, y), text, font=f, fill=fill)

    margin = int(w * (0.045 if FRAME_MODE == "ytbox" else 0.06))
    stamped((margin, top_y), "● REC", (255, 59, 48, 255))
    if label:
        lw = d.textlength(label, font=f)
        stamped((w - lw - margin, top_y), label, (255, 59, 48, 255))
    stamped((margin, bot_y), "PLAY", (255, 255, 255, 235))
    # Courier New has no ▶ glyph — draw the triangle as a polygon instead
    tri_x = margin + int(d.textlength("PLAY ", font=f))
    box = f.getbbox("P")
    tri_h = box[3] - box[1]
    tri_y = bot_y + box[1]
    d.polygon([(tri_x + 3, tri_y + tri_h + 3), (tri_x + 3, tri_y + 3),
               (tri_x + 3 + int(tri_h * 0.9), tri_y + 3 + tri_h // 2)], fill=(0, 0, 0, 150))
    d.polygon([(tri_x, tri_y + tri_h), (tri_x, tri_y),
               (tri_x + int(tri_h * 0.9), tri_y + tri_h // 2)], fill=(255, 255, 255, 235))
    tw = d.textlength(date_text, font=f)
    stamped((w - tw - margin, bot_y), date_text, (255, 255, 255, 235))

    if FRAME_MODE == "ytbox":
        # ── fake YouTube player chrome under the strip ──
        AB = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
        AR = "/System/Library/Fonts/Supplemental/Arial.ttf"
        title_f = ImageFont.truetype(AB, 46)
        meta_f  = ImageFont.truetype(AR, 30)
        pill_f  = ImageFont.truetype(AB, 30)
        pad = 40
        ty = strip_y + strip_h + 38
        t = yt_title or "growth tapes"
        while d.textlength(t + "…", font=title_f) > w - 2 * pad and len(t) > 8:
            t = t[:-1]
        d.text((pad, ty), t if t == (yt_title or "growth tapes") else t + "…",
               font=title_f, fill=(255, 255, 255, 255))
        d.text((pad, ty + 68), "@selfImprovement  1.2K views  2d ago",
               font=meta_f, fill=(170, 170, 170, 255))
        ry = ty + 132
        av = Path.home() / "agentic_os" / "growth_avatar.jpg"
        if av.exists():
            a = Image.open(av).convert("RGB")
            s = min(a.size)
            a = a.crop(((a.width - s) // 2, (a.height - s) // 2,
                        (a.width + s) // 2, (a.height + s) // 2)).resize((72, 72))
            m = Image.new("L", (72, 72), 0)
            ImageDraw.Draw(m).ellipse([0, 0, 72, 72], fill=255)
            img.paste(a, (pad, ry), m)
        else:
            d.ellipse([pad, ry, pad + 72, ry + 72], fill=(140, 30, 30, 255))
            gw = d.textlength("G", font=pill_f)
            d.text((pad + 36 - gw / 2, ry + 20), "G", font=pill_f, fill=(255, 255, 255, 255))
        sub_x = pad + 96
        d.rounded_rectangle([sub_x, ry + 2, sub_x + 232, ry + 70], radius=34,
                            fill=(255, 255, 255, 255))
        sw = d.textlength("Subscribe", font=pill_f)
        d.text((sub_x + 116 - sw / 2, ry + 20), "Subscribe", font=pill_f, fill=(15, 15, 15, 255))
        lx = sub_x + 262
        d.rounded_rectangle([lx, ry + 2, lx + 236, ry + 70], radius=34, fill=(45, 45, 45, 255))
        px, py = lx + 26, ry + 18
        d.polygon([(px + 10, py + 14), (px + 17, py + 1), (px + 23, py + 5), (px + 19, py + 14),
                   (px + 34, py + 14), (px + 36, py + 32), (px + 10, py + 36)],
                  fill=(255, 255, 255, 255))
        d.text((px + 48, py + 4), "15", font=pill_f, fill=(255, 255, 255, 255))
        d.line([lx + 128, ry + 16, lx + 128, ry + 56], fill=(90, 90, 90, 255), width=2)
        qx, qy = lx + 158, ry + 20
        d.polygon([(qx + 26, qy + 22), (qx + 19, qy + 35), (qx + 13, qy + 31), (qx + 17, qy + 22),
                   (qx + 2, qy + 22), (qx, qy + 4), (qx + 26, qy)],
                  fill=(255, 255, 255, 255))
        sx = lx + 260
        d.rounded_rectangle([sx, ry + 2, sx + 120, ry + 70], radius=34, fill=(45, 45, 45, 255))
        ax, ay = sx + 34, ry + 16
        d.polygon([(ax, ay + 22), (ax + 22, ay + 22), (ax + 22, ay + 34), (ax + 44, ay + 16),
                   (ax + 22, ay - 2), (ax + 22, ay + 10), (ax, ay + 12)],
                  fill=(255, 255, 255, 255))

    img.save(path)


def apply_vhs_overlay(inp: Path, out: Path, date_text: str = VHS_DEFAULT_DATE,
                      label: str = "", yt_title: str = "") -> None:
    """Composite the camcorder UI over the video + add light analog tape noise."""
    overlay_png = inp.parent / "vhs_overlay.png"
    _build_vhs_overlay_png(overlay_png, date_text, OUT_W, OUT_H, label, yt_title)
    cmd = [
        FFMPEG, "-y",
        "-i", str(inp),
        "-i", str(overlay_png),
        "-filter_complex", "[0:v]noise=alls=4:allf=t[nv];[nv][1:v]overlay=0:0[v]",
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"VHS overlay failed: {r.stderr[-500:]}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 content_pipeline.py script.txt")
        sys.exit(1)

    script_path = Path(sys.argv[1])
    meta, segments = parse_script(script_path)

    # Per-script overrides from header block
    game          = meta.get("game", GAME_TAG)
    title         = meta.get("title", script_path.stem)
    music_q       = meta.get("music", MUSIC_QUERY)
    b_source      = meta.get("broll", BROLL_SOURCE)
    story_theme   = meta.get("theme", "")
    captions_on   = meta.get("captions", "true").lower() not in ("false", "no", "0", "off")
    voice_profile = meta.get("voice", "adam").lower()
    caption_style = meta.get("caption_style", "default").lower()
    music_delay   = float(meta.get("music_delay", MUSIC_DELAY_DEFAULT))
    orientation   = meta.get("orientation", "portrait").lower()
    global OUT_W, OUT_H, FRAME_MODE
    if orientation in ("landscape", "horizontal", "16:9"):
        # 16:9 output — every downstream helper (slice crop, assemble,
        # VHS overlay, Ken Burns) reads these module globals
        OUT_W, OUT_H = 1920, 1080
    elif orientation in ("ytbox", "youtube", "boxed"):
        # vanshon-style: portrait canvas, full 16:9 footage strip centered
        # between black bars, fake YouTube player chrome under the strip
        FRAME_MODE = "ytbox"
    # Optional per-speaker voice map. Header format:
    #   voices: N=bill_horror,V=sarah_horror
    # When a segment has a speaker tag ([N]/[V]), look it up here; otherwise
    # fall back to the script's default `voice:` field.
    voices_map: dict[str, str] = {}
    voices_raw = meta.get("voices", "")
    if voices_raw:
        for pair in voices_raw.split(","):
            if "=" in pair:
                spk, prof = pair.split("=", 1)
                voices_map[spk.strip().upper()] = prof.strip().lower()
    template_pool = meta.get("template_pool", "aita").lower()

    # Choose output folder by pool so AITA and Stoic renders stay separate
    folder_by_pool = {"aita": "AITA Renders", "stoic": "Stoic Renders", "horror": "Horror Renders"}
    out_dir = Path.home() / "Desktop" / folder_by_pool.get(template_pool, "AITA Renders")
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / f"{title}.mp4"

    # Random template by default — pick fresh B-roll category + music track per
    # render so videos don't all look identical. Set RANDOM_TEMPLATE=0 to use
    # the script's `game:` and `music:` headers instead.
    # AI mode generates visuals per-segment — random template selection doesn't apply
    random_template_on = os.environ.get("RANDOM_TEMPLATE", "1") != "0" and b_source != "ai"
    template_label = None
    if random_template_on:
        try:
            rand_cat, rand_music = pick_random_template(pool=template_pool)
            game = rand_cat
            b_source = "local"
            import shutil as _sh
            _sh.copyfile(rand_music, MUSIC_PATH)
            _strip_leading_silence(MUSIC_PATH)
            template_label = f"{rand_cat} + {rand_music.stem}"
            print(f"🎲 Random template [{template_pool}]: {template_label}")
            Path("/tmp/last_template.txt").write_text(template_label + "\n")
        except Exception as e:
            print(f"⚠ random template unavailable, falling back to header: {e}")
            random_template_on = False

    if not random_template_on:
        fetch_bg_music(music_q)

    print(f"Script:  {script_path.name}")
    print(f"Game:    {game or '(none)'}")
    print(f"Title:   {title}")
    print(f"B-roll:  {b_source}")
    print(f"Segments: {len(segments)}")

    with tempfile.TemporaryDirectory(prefix="content_") as tmp:
        tmp = Path(tmp)
        segment_files = []
        segment_durs: list[float] = []

        for i, seg in enumerate(segments):
            print(f"\n[{i+1}/{len(segments)}] \"{seg['text'][:60]}...\"")

            audio_path  = tmp / f"audio_{i}.mp3"
            broll_path  = tmp / f"broll_{i}.mp4"
            segment_out = tmp / f"segment_{i}.mp4"

            # Per-segment voice — speaker tag in script overrides default
            seg_voice = voices_map.get(seg.get("speaker") or "", voice_profile)
            print(f"  → Voiceover [{seg_voice}]…")
            generate_voiceover(seg["text"], audio_path, voice_profile=seg_voice)
            dur = get_duration(audio_path)
            segment_durs.append(dur)
            # Final segment carries a music-only outro — B-roll keeps rolling
            tail = OUTRO_TAIL if i == len(segments) - 1 else 0.0
            need = dur + tail
            if b_source == "ai":
                print(f"  → AI image (need {need:.1f}s)…")
                fetch_broll_ai_image(seg["text"], story_theme, need, broll_path)
            elif b_source == "youtube":
                print(f"  → B-roll [youtube] ({seg['keyword']}, need {need:.1f}s)…")
                fetch_broll_youtube(game, seg["keyword"], need, broll_path)
            elif b_source == "local":
                print(f"  → B-roll [local] ({seg['keyword']}, need {need:.1f}s)…")
                fetch_broll_local(game, need, broll_path)
            else:
                print(f"  → B-roll [pexels] ({seg['keyword']}, need {need:.1f}s)…")
                fetch_broll(seg["keyword"], need, broll_path)
            print(f"  → Assembling…")
            assemble_segment(audio_path, broll_path, seg["text"], segment_out, tail=tail)
            segment_files.append(segment_out)

        # Concatenate all segments
        print("\nConcatenating segments…")
        concat_list = tmp / "concat.txt"
        concat_list.write_text("\n".join(f"file '{f}'" for f in segment_files))
        raw_out = tmp / "raw.mp4"
        cmd_concat = [
            FFMPEG, "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-c:v", "libx264", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            str(raw_out),
        ]
        r = subprocess.run(cmd_concat, capture_output=True, text=True)
        if r.returncode != 0:
            print("ERROR:", r.stderr[-2000:])
            sys.exit(1)

        # Color grade — music bed enters after music_delay seconds (default 4.0)
        print("Applying color grade…")
        graded = tmp / "graded.mp4" if captions_on else output
        ambience = meta.get("ambience", "static" if caption_style == "vhs" else "none").lower()
        apply_grade(raw_out, graded, music_delay=music_delay, ambience=ambience)

        if not captions_on and caption_style == "vhs":
            print("Applying VHS overlay…")
            vhs_only = tmp / "vhs.mp4"
            apply_vhs_overlay(graded, vhs_only, meta.get("vhs_date", VHS_DEFAULT_DATE),
                              meta.get("vhs_label", ""), meta.get("yt_title", ""))
            vhs_only.replace(output)

        # Burn-in captions (skippable via `captions: false` header)
        if captions_on:
            print("Burning in captions…")
            captioned = tmp / "captioned.mp4"
            try:
                burn_captions(graded, captioned, segments, segment_durs, style=caption_style)
            except Exception as e:
                print(f"  Caption burn failed ({e}) — saving un-captioned video instead")
                graded.replace(output)
            else:
                if caption_style == "vhs":
                    print("Applying VHS overlay…")
                    vhs_out = tmp / "vhs.mp4"
                    try:
                        apply_vhs_overlay(captioned, vhs_out, meta.get("vhs_date", VHS_DEFAULT_DATE),
                                          meta.get("vhs_label", ""), meta.get("yt_title", ""))
                        captioned = vhs_out
                    except Exception as e:
                        print(f"  VHS overlay failed ({e}) — continuing without")
                # Captacity/moviepy writes mp3-in-mp4 which iOS silently mutes.
                # Re-encode audio to AAC (faststart so it plays inline on phones).
                print("Re-muxing audio to AAC (iOS compatibility)…")
                fix_cmd = [
                    FFMPEG, "-y", "-i", str(captioned),
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart",
                    str(output),
                ]
                r = subprocess.run(fix_cmd, capture_output=True, text=True)
                if r.returncode != 0:
                    print(f"  AAC re-mux failed — saving captioned mp3 output instead")
                    captioned.replace(output)

    size_mb = output.stat().st_size / 1_000_000
    total_dur = get_duration(output)
    print(f"\nDone! {output}  ({size_mb:.1f} MB, {total_dur:.1f}s)")


if __name__ == "__main__":
    main()
