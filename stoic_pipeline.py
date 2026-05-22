#!/usr/bin/env python3
"""Stoic / motivational pipeline — parallel to aita_pipeline.py.

Sources public-domain philosophy texts (Marcus Aurelius, Seneca, Epictetus,
Lao Tzu, etc.), picks a passage, builds a script.txt, hands off to
content_pipeline.py for rendering (with voice=bill + template_pool=stoic).

Usage:
    python3 stoic_pipeline.py download           Pull all public-domain texts (one-time)
    python3 stoic_pipeline.py list               Show available passages
    python3 stoic_pipeline.py fetch              Pick N passages (today's cache)
    python3 stoic_pipeline.py render <1-5>       Render passage N from today's cache
    python3 stoic_pipeline.py random             Pick + render one passage end-to-end
"""

import json
import random
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

CORPUS_DIR  = Path.home() / "agentic_os" / "stoic_corpus"
CACHE_DIR   = Path.home() / "agentic_os" / "stoic_cache"
SCRIPT_DIR  = Path.home() / "agentic_os"

PASSAGE_MIN_CHARS = 350
PASSAGE_MAX_CHARS = 1000  # ~60-90s narrated at Bill's pace
SENTENCES_PER_PASSAGE = (4, 9)  # min, max sentence count


# Public-domain sources. Each: (text_id, display_name, author, url)
# URLs point to Project Gutenberg plain-text files.
SOURCES = [
    ("meditations",  "Meditations",            "Marcus Aurelius",
     "https://www.gutenberg.org/files/2680/2680-0.txt"),
    ("seneca",       "Moral Letters to Lucilius", "Seneca",
     "https://www.gutenberg.org/cache/epub/56075/pg56075.txt"),
    ("enchiridion",  "Enchiridion",            "Epictetus",
     "https://www.gutenberg.org/cache/epub/45109/pg45109.txt"),
    ("tao_te_ching", "Tao Te Ching",           "Lao Tzu",
     "https://www.gutenberg.org/cache/epub/216/pg216.txt"),
    ("dhammapada",   "The Dhammapada",         "Buddha (trans. Müller)",
     "https://www.gutenberg.org/cache/epub/2017/pg2017.txt"),
]

USER_AGENT = "PAIS-Stoic-Pipeline/1.0"


# ─── Reddit motivational source (primary) ─────────────────────────────────────
# Each post becomes a script: title → hook, selftext → body, motivational CTA outro.
MOTIVATIONAL_SUBS = [
    "GetMotivated",        # main motivational sub — essays, lessons learned
    "DecidingToBeBetter",  # self-improvement narratives
    "Stoicism",            # philosophy reflections + applied stoicism
    "selfimprovement",     # practical advice posts
]
SUB_SHORT_MOT = {
    "GetMotivated":       "MOT",
    "DecidingToBeBetter": "DTBB",
    "Stoicism":           "STOIC",
    "selfimprovement":    "SI",
}
REDDIT_MIN_UPS    = 200   # these subs are smaller than AITA
REDDIT_MIN_BODY   = 400
REDDIT_MAX_BODY   = 3000
REDDIT_BODY_TGT   = 1000


def _fetch_reddit_sub(sub: str) -> list[dict]:
    url = f"https://www.reddit.com/r/{sub}/hot.json?limit=25"
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        r.raise_for_status()
        return r.json()["data"]["children"]
    except Exception:
        return []


def fetch_motivational_posts(subs: list[str] | None = None) -> list[dict]:
    """Pull qualifying posts from r/GetMotivated, r/Stoicism, etc.

    Filters: upvotes >= 200, body 400-3000 chars, not stickied/NSFW/video/image-only.
    Each post is tagged with `sub` so build_script can adapt CTA per sub.
    """
    if subs is None:
        subs = MOTIVATIONAL_SUBS
    out = []
    for sub in subs:
        for child in _fetch_reddit_sub(sub):
            d = child["data"]
            if d.get("stickied") or d.get("over_18") or d.get("is_video"):
                continue
            if d.get("ups", 0) < REDDIT_MIN_UPS:
                continue
            body = d.get("selftext", "")
            if not (REDDIT_MIN_BODY <= len(body) <= REDDIT_MAX_BODY):
                continue
            out.append({
                "id":     d["id"],
                "title":  d["title"],
                "body":   body,
                "ups":    d["ups"],
                "ratio":  d.get("upvote_ratio", 0.0),
                "url":    f"https://reddit.com{d['permalink']}",
                "sub":    sub,
            })
    return out


def score_reddit_post(post: dict) -> float:
    """Cheap ranking signal — favor upvotes, ratio, and tight titles."""
    bonus = 0.0
    if 30 <= len(post["title"]) <= 120:
        bonus += 0.5
    if post.get("ratio", 0) >= 0.9:
        bonus += 1.0
    return (post["ups"] / 1000.0) + bonus * 4


# ─── Corpus download ──────────────────────────────────────────────────────────

def download_corpus(force: bool = False) -> dict:
    """Download all sources to CORPUS_DIR. Returns status dict."""
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    status = {}
    for tid, name, author, url in SOURCES:
        target = CORPUS_DIR / f"{tid}.txt"
        if target.exists() and not force:
            status[tid] = f"cached ({target.stat().st_size // 1024} KB)"
            continue
        print(f"  ↓ {name} by {author}…")
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
            r.raise_for_status()
            # Strip Gutenberg header/footer if present
            text = r.text
            start_marker = "*** START OF "
            end_marker = "*** END OF "
            if start_marker in text:
                text = text.split(start_marker, 1)[1].split("***", 1)[1] if "***" in text.split(start_marker, 1)[1] else text
            if end_marker in text:
                text = text.split(end_marker, 1)[0]
            target.write_text(text.strip())
            status[tid] = f"downloaded ({target.stat().st_size // 1024} KB)"
        except Exception as e:
            status[tid] = f"FAILED: {e}"
    return status


# ─── Passage extraction ───────────────────────────────────────────────────────

def split_into_paragraphs(text: str) -> list[str]:
    """Split a public-domain text into clean paragraphs."""
    # Normalize whitespace, split on double-newlines
    text = re.sub(r"\r\n", "\n", text)
    raw = re.split(r"\n\s*\n+", text)
    paragraphs = []
    for p in raw:
        # Join soft-wrapped lines within a paragraph
        cleaned = " ".join(line.strip() for line in p.splitlines() if line.strip())
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            continue
        # Filter out chapter headings, footnotes, brackets-only lines
        if re.match(r"^(BOOK|CHAPTER|PART|VOLUME|FOOTNOTE|\[|LETTER)\b", cleaned, re.I):
            continue
        if len(cleaned) < 50:
            continue  # likely a heading
        paragraphs.append(cleaned)
    return paragraphs


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p.strip() and len(p.strip()) > 5]


def clean_passage(text: str) -> str:
    """Strip Roman numeral chapter markers + normalize for TTS."""
    # Strip leading "XXXVI." / "XII." / "IV." style chapter markers
    text = re.sub(r"^[IVXLCDM]+\.\s+", "", text)
    # Strip "Section N." / "Chapter N." style
    text = re.sub(r"^(Section|Chapter|Book|Letter)\s+[IVXLCDM\d]+\.\s+", "", text, flags=re.I)
    # Strip markdown-style emphasis (Gutenberg uses _word_ for italics)
    text = re.sub(r"_([^_]+)_", r"\1", text)
    # Strip footnote refs like [1] [42] [a]
    text = re.sub(r"\[[\w\d]+\]", "", text)
    # Collapse spaces
    text = re.sub(r"\s+", " ", text).strip()
    return text


def is_good_passage(text: str) -> bool:
    """Filter out junk passages — too short, too long, all-caps, non-ASCII heavy."""
    n = len(text)
    if n < PASSAGE_MIN_CHARS or n > PASSAGE_MAX_CHARS * 2:
        return False
    sentences = split_sentences(text)
    if not (SENTENCES_PER_PASSAGE[0] <= len(sentences) <= SENTENCES_PER_PASSAGE[1] + 3):
        return False
    upper_ratio = sum(1 for c in text if c.isupper()) / max(1, len(text))
    if upper_ratio > 0.25:
        return False
    # Reject Greek/foreign-script-heavy passages — TTS pronounces them poorly
    non_ascii_ratio = sum(1 for c in text if ord(c) > 127) / max(1, len(text))
    if non_ascii_ratio > 0.03:
        return False
    return True


def trim_to_target(text: str, max_chars: int = PASSAGE_MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    sentences = split_sentences(text)
    out = ""
    for s in sentences:
        if len(out) + len(s) + 1 > max_chars:
            break
        out = (out + " " + s).strip()
    return out or text[:max_chars].rstrip() + "..."


def load_all_passages() -> list[dict]:
    """Load every text in CORPUS_DIR, split into good passages, tag with source."""
    if not CORPUS_DIR.exists():
        return []
    passages = []
    source_map = {tid: (name, author) for tid, name, author, _ in SOURCES}
    for fp in sorted(CORPUS_DIR.glob("*.txt")):
        tid = fp.stem
        source_name, author = source_map.get(tid, (tid, "Unknown"))
        text = fp.read_text(errors="ignore")
        for p in split_into_paragraphs(text):
            cleaned = clean_passage(p)
            if is_good_passage(cleaned):
                passages.append({
                    "id":     f"{tid}_{abs(hash(cleaned[:50])) % 100000:05d}",
                    "source": source_name,
                    "author": author,
                    "text":   trim_to_target(cleaned),
                })
    return passages


# ─── Script building (mirrors aita_pipeline.build_script) ─────────────────────

OUTRO_CTAS = [
    "Follow for daily wisdom from the greats.",
    "Save this if it spoke to you.",
    "Tag someone who needs to hear this today.",
    "Which line hit hardest? Comment below.",
    "Follow for more.",
]


def build_script(passage: dict) -> Path:
    """Write a script.txt for a single passage. Returns the script path."""
    text = passage["text"]
    sentences = split_sentences(text)

    # Lead with the source attribution as the first spoken line so the viewer
    # knows whose words they're hearing
    attribution = f"{passage['author']}."

    outro = random.choice(OUTRO_CTAS)

    safe_id = passage["id"]
    script_path = SCRIPT_DIR / f"stoic_{safe_id}.txt"
    lines = [
        "---",
        "voice:          bill",
        "template_pool:  stoic",
        f"title:          stoic_{safe_id}",
        "broll:          local",
        "captions:       true",
        "---",
        attribution,
        *sentences,
        outro,
    ]
    script_path.write_text("\n".join(lines))
    return script_path


def _clean_reddit_body(body: str, max_chars: int = REDDIT_BODY_TGT) -> str:
    """Strip soft wraps, edit/update tails, markdown, then truncate at sentence."""
    body = re.sub(r"\r\n", "\n", body)
    body = re.sub(r"\n+", " ", body)
    body = re.sub(r"\s+", " ", body).strip()
    body = re.split(r"\b(EDIT|UPDATE|ETA|TL;DR|TLDR)\b", body, maxsplit=1, flags=re.I)[0].strip()
    body = re.sub(r"_([^_]+)_", r"\1", body)        # strip _italic_
    body = re.sub(r"\*\*([^*]+)\*\*", r"\1", body)  # strip **bold**
    body = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", body)  # strip [text](url) → text
    body = re.sub(r"https?://\S+", "", body)        # strip raw URLs
    if len(body) <= max_chars:
        return body
    truncated = body[:max_chars]
    last_period = truncated.rfind(".")
    if last_period > max_chars - 250:
        return truncated[:last_period + 1]
    return truncated.rstrip() + "..."


def build_reddit_script(post: dict) -> Path:
    """Build a script.txt from a Reddit motivational post."""
    title = post["title"].strip().rstrip(".?!")
    body = _clean_reddit_body(post["body"])
    sentences = split_sentences(body)

    outro = random.choice(OUTRO_CTAS)
    script_path = SCRIPT_DIR / f"stoic_reddit_{post['id']}.txt"
    lines = [
        "---",
        "voice:          bill",
        "template_pool:  stoic",
        f"title:          stoic_reddit_{post['id']}",
        "broll:          local",
        "captions:       true",
        "---",
        f"{title}.",
        *sentences,
        outro,
    ]
    script_path.write_text("\n".join(lines))
    return script_path


def render_script(script_path: Path) -> Path:
    """Invoke content_pipeline.py and return the resulting .mp4 path."""
    subprocess.run(
        ["python3", str(SCRIPT_DIR / "content_pipeline.py"), str(script_path)],
        check=True,
    )
    # Output landing — Stoic pool routes to ~/Desktop/Stoic Renders/
    title = script_path.stem
    for line in script_path.read_text().splitlines():
        if line.strip().startswith("title:"):
            title = line.split(":", 1)[1].strip()
            break
    return Path.home() / "Desktop" / "Stoic Renders" / f"{title}.mp4"


# ─── Cache (today's picks for /stoic command) ─────────────────────────────────

def _today_cache_path() -> Path:
    return CACHE_DIR / f"{time.strftime('%Y-%m-%d')}.json"


def save_picks_for_today(picks: list[dict]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _today_cache_path().write_text(json.dumps(picks, indent=2))


def load_today_picks() -> list[dict] | None:
    f = _today_cache_path()
    return json.loads(f.read_text()) if f.exists() else None


def fetch_top_passages(n: int = 5) -> list[dict]:
    """Pick N random passages from the corpus (no global ranking signal exists)."""
    all_p = load_all_passages()
    if not all_p:
        return []
    random.shuffle(all_p)
    return all_p[:n]


# ─── CLI ──────────────────────────────────────────────────────────────────────

def cmd_download(_args):
    status = download_corpus()
    print("\n=== Corpus download status ===")
    for tid, msg in status.items():
        print(f"  {tid:15s} → {msg}")
    return 0


def cmd_list(_args):
    passages = load_all_passages()
    if not passages:
        print("No corpus loaded. Run `stoic_pipeline.py download` first.")
        return 1
    print(f"{len(passages)} passages available across {len({p['source'] for p in passages})} works:")
    for src in sorted({p["source"] for p in passages}):
        count = sum(1 for p in passages if p["source"] == src)
        print(f"  {count:>4} from {src}")
    return 0


def cmd_fetch(args):
    n = int(args[0]) if args else 5
    picks = fetch_top_passages(n)
    if not picks:
        print("No passages available. Run `stoic_pipeline.py download` first.")
        return 1
    save_picks_for_today(picks)
    print(f"\nToday's {len(picks)} Stoic picks:\n")
    for i, p in enumerate(picks, 1):
        print(f"  {i}. {p['author']} — {p['source']}")
        print(f"     \"{p['text'][:120]}...\"\n")
    return 0


def cmd_render(args):
    if not args:
        print("Usage: stoic_pipeline.py render <1-5>")
        return 1
    picks = load_today_picks()
    if picks is None:
        print("No cached picks for today. Run `fetch` first.")
        return 1
    n = int(args[0])
    if not (1 <= n <= len(picks)):
        print(f"Pick must be 1-{len(picks)}")
        return 1
    script = build_script(picks[n - 1])
    print(f"Built: {script}")
    out = render_script(script)
    print(f"Rendered: {out}")
    return 0


def cmd_random(_args):
    """Convenience: pick + build + render one passage end-to-end."""
    passages = load_all_passages()
    if not passages:
        print("No corpus. Run `stoic_pipeline.py download` first.")
        return 1
    pick = random.choice(passages)
    print(f"Selected: {pick['author']} — {pick['source']}")
    print(f"  \"{pick['text'][:120]}...\"\n")
    script = build_script(pick)
    out = render_script(script)
    print(f"\nRendered: {out}")
    return 0


def cmd_reddit(args):
    """Fetch from r/GetMotivated etc and render the top-ranked post end-to-end.
    Pass an integer to pick rank N (1=top scored)."""
    posts = fetch_motivational_posts()
    if not posts:
        print("No qualifying motivational posts found right now.")
        return 1
    posts.sort(key=score_reddit_post, reverse=True)
    rank = int(args[0]) if args else 1
    if not (1 <= rank <= len(posts)):
        print(f"Rank must be 1-{len(posts)}")
        return 1
    pick = posts[rank - 1]
    short = SUB_SHORT_MOT.get(pick["sub"], pick["sub"])
    print(f"\nFetched {len(posts)} posts. Top 5:")
    for i, p in enumerate(posts[:5], 1):
        s = SUB_SHORT_MOT.get(p["sub"], p["sub"])
        print(f"  {i}. [{s}] [{p['ups']:>4} ups] {p['title'][:80]}")
    print(f"\nPicked rank {rank}: [{short}] {pick['title']}")
    print(f"URL: {pick['url']}\n")
    script = build_reddit_script(pick)
    print(f"Script: {script}")
    out = render_script(script)
    print(f"Rendered: {out}")
    return 0


COMMANDS = {
    "download": cmd_download,
    "list":     cmd_list,
    "fetch":    cmd_fetch,
    "render":   cmd_render,
    "random":   cmd_random,
    "reddit":   cmd_reddit,
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    sys.exit(COMMANDS[sys.argv[1]](sys.argv[2:]) or 0)
