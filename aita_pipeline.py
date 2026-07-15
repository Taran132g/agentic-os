#!/usr/bin/env python3
"""AITA storytime pipeline.

Usage:
    python3 aita_pipeline.py fetch              Fetch top 5 hook candidates, cache them
    python3 aita_pipeline.py render <1-5>       Render the picked post via content_pipeline.py
    python3 aita_pipeline.py list               Show current day's cached picks

Daily flow (later wired to cron + Telegram):
    11am   → fetch  → top 5 hooks sent to Telegram
    user   → taps Pick #N
    PAIS   → render N → sends .mp4 back to Telegram
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

USER_AGENT     = "PAIS-AITA-Pipeline/1.0 (by /u/taranveer)"
ARCTIC_SHIFT   = "https://arctic-shift.photon-reddit.com/api/posts/search"

# Subreddits sourced for picks. AITA stays first so it dominates the pool;
# others provide variety. Each post is tagged with its source `sub` field.
SUBREDDITS = [
    "AmItheAsshole",          # AITA — original niche
    "AmIOverreacting",        # AIO — same energy, lower escalation
    "TrueOffMyChest",         # TOMC — confession/emotional
    "relationship_advice",    # RA — longer-form drama
    "BestofRedditorUpdates",  # BORU — multi-part stories, perfect for Part 1/Part 2
]
SUB_META = {
    "AmItheAsshole":         {"short": "AITA",  "outro_style": "aita"},
    "AmIOverreacting":       {"short": "AIO",   "outro_style": "generic"},
    "TrueOffMyChest":        {"short": "TOMC",  "outro_style": "generic"},
    "relationship_advice":   {"short": "RA",    "outro_style": "advice"},
    "BestofRedditorUpdates": {"short": "BORU",  "outro_style": "generic"},
}
CACHE_DIR      = Path.home() / "agentic_os" / "aita_cache"
SCRIPT_DIR     = Path.home() / "agentic_os"
BLOCKLIST_FILE = SCRIPT_DIR / "aita_blocklist.json"
MIN_UPS         = 200  # Arctic Shift returns recent posts before votes fully accumulate
MIN_BODY        = 400
MAX_BODY        = 4000
BODY_TARGET     = 1000  # ~60-90s of Adam voice at 1.0x speed
PART_THRESHOLD  = 1200  # body length above this triggers Part 1/Part 2 split
PART_MAX_CHARS  = 1100  # per-part cap so each render stays in 60-90s range


def load_blocklist() -> dict[str, dict]:
    """Map of post_id -> {title, disliked_at}. Persistent across sessions."""
    if not BLOCKLIST_FILE.exists():
        return {}
    try:
        return json.loads(BLOCKLIST_FILE.read_text())
    except Exception:
        return {}


def add_to_blocklist(entries: list[dict]) -> None:
    """entries: [{id, title}]. Adds with current timestamp; preserves existing."""
    blocked = load_blocklist()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    for e in entries:
        pid = e.get("id")
        if not pid:
            continue
        blocked[pid] = {"title": e.get("title", ""), "disliked_at": now}
    BLOCKLIST_FILE.write_text(json.dumps(blocked, indent=2))


def _fetch_one_sub(sub: str) -> list[dict]:
    """Fetch recent posts for a single subreddit via Arctic Shift mirror.

    Arctic Shift returns posts sorted by created_utc desc. We fetch 100 and
    let the caller sort by score_hook, so we always surface the best content
    from the past few days even if it hasn't hit the Reddit hot threshold yet.
    Reddit's own JSON API now 403s unauthenticated requests.
    """
    try:
        r = requests.get(
            ARCTIC_SHIFT,
            params={"subreddit": sub, "limit": 100, "sort": "desc"},
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        r.raise_for_status()
        # Arctic Shift returns flat dicts — wrap in {"data": ...} shape so
        # fetch_top_posts can treat them uniformly via p["data"].
        raw = r.json().get("data") or []
        return [{"data": p} for p in raw]
    except Exception:
        return []


def fetch_top_posts(limit: int = 5, subs: list[str] | None = None) -> list[dict]:
    """Fetch qualifying posts across all configured subs.

    Each returned post is tagged with `sub` so downstream code can adapt
    hook prefix + outro style. Blocklist + body length + upvote filters apply
    uniformly across all sources.
    """
    if subs is None:
        subs = SUBREDDITS
    blocked = load_blocklist()
    out = []
    for sub in subs:
        children = _fetch_one_sub(sub)
        for p in children:
            d = p["data"]
            if d.get("stickied") or d.get("over_18") or d.get("is_video"):
                continue
            ups = d.get("score") or d.get("ups", 0)
            if ups < MIN_UPS:
                continue
            if d["id"] in blocked:
                continue
            body = d.get("selftext", "")
            if body in ("[deleted]", "[removed]", ""):
                continue
            if not (MIN_BODY <= len(body) <= MAX_BODY):
                continue
            out.append({
                "id": d["id"],
                "title": d["title"],
                "body": body,
                "ups": ups,
                "ratio": d.get("upvote_ratio", 0.0),
                "url": f"https://reddit.com{d['permalink']}",
                "sub": sub,
            })
    return out


def clean_body(body: str, max_chars: int = BODY_TARGET) -> str:
    body = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", body)  # strip markdown links → keep label
    body = re.sub(r"https?://\S+", "", body)               # drop bare URLs
    body = re.sub(r"\n+", " ", body)
    body = re.sub(r"\s+", " ", body).strip()
    # Strip common edit/update tails
    body = re.split(r"\b(EDIT|UPDATE|ETA|TL;DR)\b", body, maxsplit=1, flags=re.I)[0].strip()
    if len(body) <= max_chars:
        return body
    truncated = body[:max_chars]
    last_period = truncated.rfind(".")
    if last_period > max_chars - 250:
        return truncated[: last_period + 1]
    return truncated.rstrip() + "..."


def split_body_into_parts(body: str) -> list[str]:
    """Split a long body into Part 1 + Part 2, preserving narrative continuity.

    Two strategies depending on body length:
    - If body fits in 2 * PART_MAX_CHARS: balanced midpoint split (no content lost).
    - If body is longer: greedy pack Part 1 up to PART_MAX_CHARS, then Part 2
      continues from there (also capped — trailing content is dropped).

    Returns a single-element list if the body is too short to split meaningfully
    (fewer than 4 sentences, or fits entirely in one part).
    """
    sentences = split_sentences(body)
    if len(sentences) < 4 or len(body) <= PART_MAX_CHARS:
        return [body]

    # Balanced split when both halves will fit comfortably
    if len(body) <= 2 * PART_MAX_CHARS:
        total = sum(len(s) for s in sentences)
        target = total / 2
        running = 0
        split_idx = len(sentences) // 2
        for i, s in enumerate(sentences):
            running += len(s)
            if running >= target:
                split_idx = max(1, i + 1)
                break
        return [
            " ".join(sentences[:split_idx]),
            " ".join(sentences[split_idx:]),
        ]

    # Greedy: fill Part 1 to the cap, then Part 2 picks up; trailing content dropped
    part1_sents: list[str] = []
    part1_len = 0
    cursor = 0
    for i, s in enumerate(sentences):
        if part1_len + len(s) + 1 > PART_MAX_CHARS and part1_sents:
            cursor = i
            break
        part1_sents.append(s)
        part1_len += len(s) + 1

    part2_sents: list[str] = []
    part2_len = 0
    for s in sentences[cursor:]:
        if part2_len + len(s) + 1 > PART_MAX_CHARS and part2_sents:
            break
        part2_sents.append(s)
        part2_len += len(s) + 1

    return [" ".join(part1_sents), " ".join(part2_sents)]


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p.strip() and len(p.strip()) > 3]


HOOK_KEYWORDS = [
    "wedding", "family", "boyfriend", "girlfriend", "wife", "husband",
    "kicked out", "refused", "told my", "ban", "ruin", "secret", "cheat",
    "in-laws", "stepmom", "stepdad", "sister", "brother", "best friend",
    "birthday", "funeral", "inheritance", "money", "rent", "pregnant",
]


def score_hook(post: dict) -> float:
    title = post["title"].lower()
    bonus = sum(0.5 for kw in HOOK_KEYWORDS if kw in title)
    if 30 <= len(post["title"]) <= 120:
        bonus += 0.5
    if post.get("ratio", 0) >= 0.9:
        bonus += 1.0
    return (post["ups"] / 1000.0) + bonus * 8


def expand_aita(text: str) -> str:
    """Expand Reddit storytime abbreviations into the words TTS should speak."""
    text = re.sub(r"\bAITAH\b", "Am I the asshole here", text, flags=re.I)
    text = re.sub(r"\bAITA\b", "Am I the asshole", text, flags=re.I)
    text = re.sub(r"\bAIO\b", "Am I overreacting", text, flags=re.I)
    text = re.sub(r"\bNTA\b", "not the asshole", text, flags=re.I)
    text = re.sub(r"\bYTA\b", "you're the asshole", text, flags=re.I)
    text = re.sub(r"\bESH\b", "everyone sucks here", text, flags=re.I)
    text = re.sub(r"\bNAH\b", "no assholes here", text, flags=re.I)
    text = re.sub(r"\bWIBTA\b", "would I be the asshole", text, flags=re.I)
    text = re.sub(r"\bOP\b", "the original poster", text, flags=re.I)
    return text


SPELL_FIX_PROMPT = (
    "Fix ONLY spelling mistakes in the text below. "
    "Do NOT fix grammar. Do NOT rewrite slang. Do NOT change sentence structure or punctuation. "
    "Do NOT add or remove any words except to correct a misspelled word. "
    "Preserve casual phrasing, run-on sentences, missing apostrophes — fix only typos. "
    "Output ONLY the corrected text. No preamble, no explanation, no quotes around it.\n\n"
    "TEXT:\n{text}"
)


def spell_fix(text: str, timeout: int = 90) -> str:
    """Run `claude -p` to fix spelling only. Falls back to original text on any failure."""
    try:
        prompt = SPELL_FIX_PROMPT.format(text=text)
        r = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode != 0:
            return text
        out = r.stdout.strip()
        # Strip common preamble patterns Claude sometimes emits despite the prompt
        for prefix in ("Here is the corrected text:", "Here's the corrected text:",
                       "Corrected text:", "Here is the text with spelling fixed:"):
            if out.lower().startswith(prefix.lower()):
                out = out[len(prefix):].strip()
        # Strip surrounding triple-backticks or quote wrappers
        if out.startswith("```") and out.endswith("```"):
            out = out.strip("`").strip()
        return out if out else text
    except Exception:
        return text


THEME_PROMPT = (
    "In ONE sentence (max 20 words), describe the visual setting of this story "
    "as a scene direction for an AI image generator: who, where, what emotional moment. "
    "No preamble. Output ONLY the scene description.\n\n"
    "Title: {title}\nStory: {body}"
)


def generate_theme(title: str, body: str, timeout: int = 45) -> str:
    """Generate a visual scene description for AI image B-roll via claude -p.

    Falls back to the post title if claude is unavailable or times out.
    """
    try:
        prompt = THEME_PROMPT.format(title=title, body=body[:300])
        r = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode == 0 and r.stdout.strip():
            out = r.stdout.strip()
            # Drop any quoted wrappers or list prefixes
            out = out.strip('"').strip("'").lstrip("- ").strip()
            return out[:200]
    except Exception:
        pass
    return title


OUTRO_BY_STYLE = {
    "aita":    "Comment not the asshole or you're the asshole below — what would you do?",
    "generic": "Comment your verdict below — what would you do?",
    "advice":  "Comment what you'd do in their shoes below.",
}
PART1_OUTRO = "Part two coming up. Follow so you don't miss the ending, and comment what you'd do so far."


def _outro_for(post: dict) -> str:
    sub = post.get("sub", "AmItheAsshole")
    style = SUB_META.get(sub, {}).get("outro_style", "generic")
    return OUTRO_BY_STYLE.get(style, OUTRO_BY_STYLE["generic"])


def _hook_for(post: dict, title: str) -> str:
    """AITA subs get the 'AITA' prefix if missing; others use title as-is."""
    sub = post.get("sub", "AmItheAsshole")
    if sub == "AmItheAsshole" and not title.lower().startswith("aita"):
        return f"AITA {title}"
    return title


def _write_script(post_id: str, safe_title: str, hook: str, sentences: list[str],
                  outro: str, part_suffix: str = "", theme: str = "") -> Path:
    """Write a single script file and return its path. part_suffix is '' or '_p1'/'_p2'."""
    script_path = SCRIPT_DIR / f"aita_{post_id}_{safe_title}{part_suffix}.txt"
    title_value = f"aita_{post_id}{part_suffix}"
    lines = [
        "---",
        "game:   ai_generated",
        f"title:  {title_value}",
        "broll:  ai",
        f"theme:  {theme or hook}",
        "music:  tense suspenseful lofi background no copyright",
        "captions: true",
        "---",
        f"{hook}.",
        *sentences,
        outro,
    ]
    script_path.write_text("\n".join(lines))
    return script_path


def build_script(post: dict, spell_check: bool = True) -> list[Path]:
    """Build one or two script files for a post.

    Returns a list of script paths:
    - Single-element list for posts <= PART_THRESHOLD chars (current behavior).
    - Two-element list [part1, part2] for longer posts, split at the sentence
      boundary nearest the midpoint. Part 1 ends on a cliffhanger CTA;
      Part 2 opens with a brief recap of the hook.
    """
    title = post["title"].strip().rstrip(".?!")
    raw_body = post["body"]
    hook = _hook_for(post, title)
    outro = _outro_for(post)

    # Decide split before truncation — use the cleaner without aggressive cap
    full_body = clean_body(raw_body, max_chars=MAX_BODY)
    should_split = len(full_body) > PART_THRESHOLD

    # Expand abbreviations FIRST so spell_fix sees the spelled-out words.
    # Otherwise claude -p sometimes "helpfully" rewrites abbreviations beyond
    # its mandate (e.g. AIO → "Am I the asshole" instead of "Am I overreacting").
    hook = expand_aita(hook)
    full_body = expand_aita(full_body)
    if spell_check:
        hook = spell_fix(hook)
        full_body = spell_fix(full_body)

    # Generate a visual scene description for AI image B-roll
    theme = generate_theme(title, raw_body[:300])

    safe_title = re.sub(r"[^a-z0-9]+", "_", title.lower())[:50].strip("_")

    if not should_split:
        body = clean_body(full_body, max_chars=BODY_TARGET)
        sentences = split_sentences(body)
        return [_write_script(post["id"], safe_title, hook, sentences, outro, theme=theme)]

    parts = split_body_into_parts(full_body)
    if len(parts) == 1:
        body = clean_body(parts[0], max_chars=BODY_TARGET)
        sentences = split_sentences(body)
        return [_write_script(post["id"], safe_title, hook, sentences, outro, theme=theme)]

    part1_body, part2_body = parts
    p1_sentences = split_sentences(part1_body)
    p2_sentences = split_sentences(part2_body)

    recap_hook = f"Part two. Quick recap: {hook}. Here's what happened next"

    return [
        _write_script(post["id"], safe_title, hook, p1_sentences, PART1_OUTRO, "_p1", theme=theme),
        _write_script(post["id"], safe_title, recap_hook, p2_sentences, outro, "_p2", theme=theme),
    ]


def render_script(script_path: Path) -> Path:
    """Render an already-built script file. Returns the output .mp4 path.

    Reads the `title:` header to compute the output location.
    Useful for the two-stage flow: build_script → user reviews → render_script.
    """
    subprocess.run(
        ["python3", str(SCRIPT_DIR / "content_pipeline.py"), str(script_path)],
        check=True,
    )
    # Parse title from header so we know where the mp4 landed
    title = script_path.stem  # fallback
    for line in script_path.read_text().splitlines():
        if line.strip().startswith("title:"):
            title = line.split(":", 1)[1].strip()
            break
    return Path.home() / "Desktop" / "AITA Renders" / f"{title}.mp4"


def cmd_fetch(_args):
    posts = fetch_top_posts()
    if not posts:
        print("No qualifying posts found. Try again later.")
        return 1
    posts.sort(key=score_hook, reverse=True)
    top5 = posts[:5]

    save_picks_for_today(top5)
    today = time.strftime("%Y-%m-%d")

    print(f"\nTop {len(top5)} hooks for {today}:\n")
    for i, p in enumerate(top5, 1):
        short = SUB_META.get(p.get("sub", ""), {}).get("short", p.get("sub", "?"))
        print(f"  {i}. [{short:>4}] [{p['ups']:>5} ups | {int(p['ratio']*100)}%] {p['title']}")
        print(f"     {p['url']}\n")
    print(f"Render with: python3 aita_pipeline.py render <1-{len(top5)}>")
    return 0


def cmd_list(_args):
    today = time.strftime("%Y-%m-%d")
    f = CACHE_DIR / f"{today}.json"
    if not f.exists():
        print(f"No cached picks for {today}. Run `aita_pipeline.py fetch` first.")
        return 1
    posts = json.loads(f.read_text())
    for i, p in enumerate(posts, 1):
        print(f"  {i}. [{p['ups']:>5} ups] {p['title']}")
    return 0


def load_today_picks() -> list[dict] | None:
    """Return today's cached picks list, or None if no cache exists."""
    today = time.strftime("%Y-%m-%d")
    f = CACHE_DIR / f"{today}.json"
    if not f.exists():
        return None
    return json.loads(f.read_text())


def _shown_path() -> Path:
    return CACHE_DIR / f"{time.strftime('%Y-%m-%d')}_shown.json"


def shown_today() -> set[str]:
    """Set of reddit post IDs already surfaced to Taran today."""
    f = _shown_path()
    if not f.exists():
        return set()
    try:
        return set(json.loads(f.read_text()))
    except Exception:
        return set()


def _mark_shown(ids: list[str]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    existing = shown_today()
    existing.update(ids)
    _shown_path().write_text(json.dumps(sorted(existing)))


def reset_shown_today() -> int:
    """Clear today's shown-IDs list. Returns the number of IDs cleared.

    Persistent blocklist (disliked hooks) is preserved — only the per-day
    "I've seen this in today's picker" filter is wiped.
    """
    f = _shown_path()
    if not f.exists():
        return 0
    try:
        n = len(json.loads(f.read_text()))
    except Exception:
        n = 0
    f.unlink(missing_ok=True)
    return n


def save_picks_for_today(picks: list[dict]) -> None:
    """Persist today's picker payload and remember which IDs we've already shown."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    (CACHE_DIR / f"{today}.json").write_text(json.dumps(picks, indent=2))
    _mark_shown([p["id"] for p in picks])


def fetch_fresh_picks(n: int = 4) -> list[dict]:
    """Top N hooks from r/AITA hot feed, excluding any post IDs already shown today."""
    posts = fetch_top_posts()
    shown = shown_today()
    fresh = [p for p in posts if p["id"] not in shown]
    fresh.sort(key=score_hook, reverse=True)
    return fresh[:n]


def render_pick(pick: int) -> list[Path]:
    """Render pick N (1-5) from today's cache. Returns the resulting .mp4 paths.

    Long posts produce two .mp4s (Part 1 + Part 2); short posts produce one.

    Raises FileNotFoundError if there's no cache, ValueError if pick is out of range,
    and subprocess.CalledProcessError if the render fails.
    """
    posts = load_today_picks()
    if posts is None:
        raise FileNotFoundError("No cached picks for today. Run fetch first.")
    if not (1 <= pick <= len(posts)):
        raise ValueError(f"Pick must be 1-{len(posts)}")

    post = posts[pick - 1]
    script_paths = build_script(post)
    return [render_script(p) for p in script_paths]


def cmd_render(args):
    if not args:
        print("Usage: aita_pipeline.py render <1-5>")
        return 1
    try:
        outs = render_pick(int(args[0]))
    except FileNotFoundError as e:
        print(str(e))
        return 1
    except ValueError as e:
        print(str(e))
        return 1
    if len(outs) == 1:
        print(f"\nRendered: {outs[0]}")
    else:
        print(f"\nRendered {len(outs)} parts:")
        for i, p in enumerate(outs, 1):
            print(f"  Part {i}: {p}")
    return 0


COMMANDS = {"fetch": cmd_fetch, "render": cmd_render, "list": cmd_list}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    sys.exit(COMMANDS[sys.argv[1]](sys.argv[2:]) or 0)
