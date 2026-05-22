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
import requests

# ── Config ─────────────────────────────────────────────────────────────────────
FFMPEG         = "/opt/homebrew/bin/ffmpeg"
FFPROBE        = "/opt/homebrew/bin/ffprobe"
OUT_W, OUT_H   = 1080, 1920
FONT_PATH      = "/System/Library/Fonts/Helvetica.ttc"
VOICE_ID       = "pNInz6obpgDQGcFmaJgB"  # ElevenLabs "Adam" — deep American male, AITA-tier default
VOICE_SPEED    = 1.0                       # storytime cadence — faster than introspective

# Voice profiles selectable per-script via `voice:` header field.
# Each profile: (voice_id, speed, stability, similarity_boost)
VOICE_PROFILES = {
    "adam":          ("pNInz6obpgDQGcFmaJgB", 1.0,  0.72, 0.75),  # AITA storytime default
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
MUSIC_PATH     = Path.home() / "agentic_os" / "bg_music.mp3"
BROLL_SOURCE   = "youtube"    # "youtube", "pexels", or "local"
GAME_TAG       = "LittleBigPlanet"  # prepended to yt-dlp searches
BROLL_CACHE    = Path.home() / "agentic_os" / "broll_cache"  # local pre-downloaded loops
MUSIC_CACHE    = Path.home() / "agentic_os" / "music_cache"  # royalty-free track library
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

    # Pick shortest clip that is long enough
    candidates = [v for v in videos if v["duration"] >= duration_needed]
    if not candidates:
        candidates = videos  # fallback: use whatever is available

    clip = random.choice(candidates[:5])  # randomise from top 5 matches

    # Prefer HD file
    files = sorted(clip["video_files"], key=lambda f: f.get("width", 0), reverse=True)
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
    query_words = {w.lower() for w in mood_query.split() if len(w) > 2}
    best_track, best_score = None, -1
    for t in tracks:
        name_words = set(t.stem.lower().replace("_", " ").split())
        score = len(query_words & name_words)
        if score > best_score:
            best_score, best_track = score, t
    # If nothing overlaps, pick at random (any cached track is safer than yt-dlp)
    return best_track if best_score > 0 else random.choice(tracks)


def fetch_bg_music(query: str = MUSIC_QUERY) -> None:
    """Pick a track from MUSIC_CACHE if available, else fall back to yt-dlp."""
    import shutil

    cached = pick_cached_music(query)
    if cached is not None:
        import shutil as _sh
        _sh.copyfile(cached, MUSIC_PATH)
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
        "-c:v", "libx264", "-crf", "22",
        "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  (local trim failed — falling back to Pexels)")
        fetch_broll(category or "gameplay", duration_needed, out_path)


# ── ffprobe duration ────────────────────────────────────────────────────────────
def get_duration(path: Path) -> float:
    r = subprocess.run(
        [FFPROBE, "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    return float(r.stdout.strip())


# ── Assemble one segment ────────────────────────────────────────────────────────
def assemble_segment(audio: Path, broll: Path, caption: str, out: Path) -> None:
    """Combine B-roll + voiceover into a single segment clip. Captions added in CapCut."""
    dur = get_duration(audio)

    scale_filter = (
        f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
        f"crop={OUT_W}:{OUT_H},"
        f"setsar=1,fps=30"
    )

    cmd = [
        FFMPEG, "-y",
        "-stream_loop", "-1", "-i", str(broll),
        "-i", str(audio),
        "-filter_complex", f"[0:v]{scale_filter}[v]",
        "-map", "[v]",
        "-map", "1:a",
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
def apply_grade(inp: Path, out: Path) -> None:
    """Warm nostalgic grade + optional background music mix."""
    # Slightly warm, slightly vibrant — evokes early-era gaming/YouTube nostalgia
    vf = (
        "eq=saturation=0.88:contrast=1.03:brightness=-0.01,"
        "colorbalance=rs=0.04:gs=0.01:bs=-0.04:rm=0.02:gm=0:bm=-0.02:rh=0:gh=0:bh=0"
    )

    if MUSIC_PATH.exists():
        vid_dur = get_duration(inp)
        cmd = [
            FFMPEG, "-y",
            "-i", str(inp),
            "-stream_loop", "-1", "-i", str(MUSIC_PATH),
            "-filter_complex",
                f"[0:v]{vf}[v];"
                f"[1:a]volume={BG_MUSIC_VOL}[m];"
                f"[0:a][m]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[a]",
            "-map", "[v]",
            "-map", "[a]",
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
def burn_captions(inp: Path, out: Path, segments: list[dict], durations: list[float]) -> None:
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

    # Restrained style — fits the contemplative voice (not the loud yellow default)
    captacity.add_captions(
        video_file=str(inp),
        output_file=str(out),
        font_size=90,
        font_color="white",
        stroke_color="black",
        stroke_width=3,
        highlight_current_word=True,
        word_highlight_color="#F5B041",  # warm amber, matches color grade
        line_count=2,
        padding=60,
        shadow_strength=1.0,
        shadow_blur=0.15,
        segments=cap_segments,
        print_info=False,
    )


# ── Main ────────────────────────────────────────────────────────────────────────
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
    captions_on   = meta.get("captions", "true").lower() not in ("false", "no", "0", "off")
    voice_profile = meta.get("voice", "adam").lower()
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
    random_template_on = os.environ.get("RANDOM_TEMPLATE", "1") != "0"
    template_label = None
    if random_template_on:
        try:
            rand_cat, rand_music = pick_random_template(pool=template_pool)
            game = rand_cat
            b_source = "local"
            import shutil as _sh
            _sh.copyfile(rand_music, MUSIC_PATH)
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
            print(f"  → B-roll [{b_source}] ({seg['keyword']}, need {dur:.1f}s)…")
            if b_source == "youtube":
                fetch_broll_youtube(game, seg["keyword"], dur, broll_path)
            elif b_source == "local":
                fetch_broll_local(game, dur, broll_path)
            else:
                fetch_broll(seg["keyword"], dur, broll_path)
            print(f"  → Assembling…")
            assemble_segment(audio_path, broll_path, seg["text"], segment_out)
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

        # Color grade
        print("Applying color grade…")
        graded = tmp / "graded.mp4" if captions_on else output
        apply_grade(raw_out, graded)

        # Burn-in captions (skippable via `captions: false` header)
        if captions_on:
            print("Burning in captions…")
            captioned = tmp / "captioned.mp4"
            try:
                burn_captions(graded, captioned, segments, segment_durs)
            except Exception as e:
                print(f"  Caption burn failed ({e}) — saving un-captioned video instead")
                graded.replace(output)
            else:
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
