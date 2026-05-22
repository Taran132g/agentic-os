#!/usr/bin/env python3
"""Horror / nosleep pipeline — parallel to aita_pipeline + stoic_pipeline.

Pulls short scary stories from r/nosleep, r/LetsNotMeet, r/shortscarystories,
r/TwoSentenceHorror. Builds a script.txt, hands off to content_pipeline.py
with voice=drew_horror + template_pool=horror.

Usage:
    python3 horror_pipeline.py fetch                Pick + cache today's hooks
    python3 horror_pipeline.py list                 Show current day's cached picks
    python3 horror_pipeline.py render <1-N>         Render pick N
    python3 horror_pipeline.py reddit [rank]        Fetch + render top-scored post end-to-end
"""

import json
import random
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

CACHE_DIR  = Path.home() / "agentic_os" / "horror_cache"
SCRIPT_DIR = Path.home() / "agentic_os"
USER_AGENT = "PAIS-Horror-Pipeline/1.0"

# Subs ranked by signal strength + post format fit
HORROR_SUBS = [
    "nosleep",            # the creepypasta gold mine — long-form first-person
    "shortscarystories",  # tight format, perfect for 60s narration
    "LetsNotMeet",        # real-life creepy encounters
    "TwoSentenceHorror",  # punchline-driven, very TikTok-friendly
]
SUB_SHORT = {
    "nosleep":            "NOSLEEP",
    "shortscarystories":  "SSS",
    "LetsNotMeet":        "LNM",
    "TwoSentenceHorror":  "2SH",
}

MIN_UPS    = 150          # smaller subs than AITA
MIN_BODY   = 200          # 2SH posts can be very short
MAX_BODY   = 5000
BODY_TGT   = 1000


# ─── Reddit fetch ─────────────────────────────────────────────────────────────

def _fetch_sub(sub: str) -> list[dict]:
    url = f"https://www.reddit.com/r/{sub}/hot.json?limit=25"
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        r.raise_for_status()
        return r.json()["data"]["children"]
    except Exception:
        return []


def fetch_horror_posts(subs: list[str] | None = None) -> list[dict]:
    """Pull qualifying horror posts across configured subs."""
    if subs is None:
        subs = HORROR_SUBS
    out = []
    for sub in subs:
        for child in _fetch_sub(sub):
            d = child["data"]
            if d.get("stickied") or d.get("over_18") or d.get("is_video"):
                continue
            if d.get("ups", 0) < MIN_UPS:
                continue
            body = d.get("selftext", "")
            if not (MIN_BODY <= len(body) <= MAX_BODY):
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


def score_post(post: dict) -> float:
    """Rank by upvotes + tight-title bonus + short-form preference."""
    bonus = 0.0
    if 30 <= len(post["title"]) <= 110:
        bonus += 0.5
    if post.get("ratio", 0) >= 0.9:
        bonus += 1.0
    # Prefer shorter posts — better TikTok fit, less truncation
    if 300 <= len(post["body"]) <= 1500:
        bonus += 1.0
    return (post["ups"] / 1000.0) + bonus * 4


# ─── Script building ──────────────────────────────────────────────────────────

DIALOGUE_PROMPT = (
    "You are tagging a horror story script for two-voice TTS.\n\n"
    "Tag each sentence with a speaker:\n"
    "  [N] = NARRATOR (the storyteller — descriptions, actions, their own thoughts)\n"
    "  [V] = VICTIM/OTHER (someone other than the narrator who SPEAKS — quoted dialogue)\n\n"
    "Rules:\n"
    "- Pure narration/description = [N]\n"
    "- Quoted dialogue from someone other than the narrator = [V]\n"
    "- If a sentence has both narration AND dialogue, split into two lines\n"
    "- The narrator's own spoken dialogue still goes [N]\n"
    "- Strip the surrounding quotes from the dialogue text\n"
    "- If no dialogue exists at all, tag everything [N]\n"
    "- Preserve original wording; only split on quotation boundaries\n\n"
    "Output ONE sentence per line, each prefixed with [N] or [V]. No preamble.\n\n"
    "TEXT:\n{text}"
)


def separate_dialogue(text: str, timeout: int = 90) -> list[str]:
    """Use claude -p to tag every sentence with [N]/[V]. Returns tagged lines.

    Falls back to all-narrator lines on any failure.
    """
    sentences = split_sentences(text)
    fallback = [f"[N] {s}" for s in sentences]
    try:
        r = subprocess.run(
            ["claude", "-p", DIALOGUE_PROMPT.format(text=text)],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode != 0:
            return fallback
        lines = []
        for raw in r.stdout.splitlines():
            raw = raw.strip()
            if not raw:
                continue
            # Strip common preambles claude sometimes adds despite the prompt
            if raw.lower().startswith(("here is", "here's", "tagged:")):
                continue
            # Must start with [N] or [V]
            if re.match(r"^\[[NVnv]\]\s+", raw):
                lines.append(raw)
        return lines if lines else fallback
    except Exception:
        return fallback


OUTRO_CTAS = [
    "Follow if this gave you chills.",
    "Save this for your next sleepless night.",
    "Comment your scariest experience below.",
    "Part two coming. Follow so you don't miss it.",
    "Lights on or off? Comment below.",
]


def clean_body(body: str, max_chars: int = BODY_TGT) -> str:
    body = re.sub(r"\r\n", "\n", body)
    body = re.sub(r"\n+", " ", body)
    body = re.sub(r"\s+", " ", body).strip()
    body = re.split(r"\b(EDIT|UPDATE|TL;DR|TLDR)\b", body, maxsplit=1, flags=re.I)[0].strip()
    body = re.sub(r"_([^_]+)_", r"\1", body)
    body = re.sub(r"\*\*([^*]+)\*\*", r"\1", body)
    body = re.sub(r"\*([^*]+)\*", r"\1", body)
    body = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", body)
    body = re.sub(r"https?://\S+", "", body)
    if len(body) <= max_chars:
        return body
    truncated = body[:max_chars]
    last_period = truncated.rfind(".")
    if last_period > max_chars - 250:
        return truncated[:last_period + 1]
    return truncated.rstrip() + "..."


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p.strip() and len(p.strip()) > 3]


def build_script(post: dict, use_dialogue_split: bool = True) -> Path:
    """Write a script.txt for a single horror post.

    When use_dialogue_split is True (default), shells out to `claude -p` to tag
    each sentence with [N] (narrator) or [V] (victim/other speaker). Narrator
    speaks with Bill horror voice; victim speaks with Sarah horror voice.
    """
    title = post["title"].strip().rstrip(".?!")
    body = clean_body(post["body"])

    # Title always narrator; body gets dialogue-tagged if requested
    if use_dialogue_split:
        tagged_lines = separate_dialogue(body)
    else:
        tagged_lines = [f"[N] {s}" for s in split_sentences(body)]

    outro = random.choice(OUTRO_CTAS)
    script_path = SCRIPT_DIR / f"horror_{post['id']}.txt"
    lines = [
        "---",
        "voice:          callum_horror",
        "voices:         N=callum_horror,V=jessica_horror",
        "template_pool:  horror",
        f"title:          horror_{post['id']}",
        "broll:          local",
        "captions:       true",
        "---",
        f"[N] {title}.",
        *tagged_lines,
        f"[N] {outro}",
    ]
    script_path.write_text("\n".join(lines))
    return script_path


def script_body_for_preview(script_path: Path) -> str:
    """Read a script file and return the body lines (no frontmatter) for Telegram preview."""
    raw = script_path.read_text()
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return raw.strip()


def send_telegram_text(text: str, prefix: str = "") -> bool:
    """Send a plain-text message to Telegram. Returns True on success."""
    import os
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    msg = (prefix + text)[:4000]  # Telegram message cap
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat, "text": msg},
            timeout=15,
        )
        return r.ok
    except Exception:
        return False


def render_script(script_path: Path) -> Path:
    subprocess.run(
        ["python3", str(SCRIPT_DIR / "content_pipeline.py"), str(script_path)],
        check=True,
    )
    title = script_path.stem
    for line in script_path.read_text().splitlines():
        if line.strip().startswith("title:"):
            title = line.split(":", 1)[1].strip()
            break
    return Path.home() / "Desktop" / "Horror Renders" / f"{title}.mp4"


# ─── Cache ────────────────────────────────────────────────────────────────────

def _today_cache_path() -> Path:
    return CACHE_DIR / f"{time.strftime('%Y-%m-%d')}.json"


def save_picks_for_today(picks: list[dict]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _today_cache_path().write_text(json.dumps(picks, indent=2))


def load_today_picks() -> list[dict] | None:
    f = _today_cache_path()
    return json.loads(f.read_text()) if f.exists() else None


# ─── CLI ──────────────────────────────────────────────────────────────────────

def cmd_fetch(_args):
    posts = fetch_horror_posts()
    if not posts:
        print("No qualifying horror posts right now.")
        return 1
    posts.sort(key=score_post, reverse=True)
    top5 = posts[:5]
    save_picks_for_today(top5)
    today = time.strftime("%Y-%m-%d")
    print(f"\nTop {len(top5)} horror hooks for {today}:\n")
    for i, p in enumerate(top5, 1):
        s = SUB_SHORT.get(p["sub"], p["sub"])
        print(f"  {i}. [{s:>8}] [{p['ups']:>4} ups] {p['title'][:90]}")
    return 0


def cmd_list(_args):
    picks = load_today_picks()
    if picks is None:
        print("No cached picks. Run `horror_pipeline.py fetch`.")
        return 1
    for i, p in enumerate(picks, 1):
        s = SUB_SHORT.get(p["sub"], p["sub"])
        print(f"  {i}. [{s}] [{p['ups']:>4} ups] {p['title']}")
    return 0


def cmd_render(args):
    if not args:
        print("Usage: horror_pipeline.py render <1-5>")
        return 1
    picks = load_today_picks()
    if picks is None:
        print("No cached picks. Run `fetch` first.")
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


def cmd_preview(args):
    """Fetch + build dialogue-tagged script + send to Telegram for approval. No render.

    Usage: horror_pipeline.py preview [rank]
    Then run `horror_pipeline.py render-id <post_id>` to actually render after approving.
    """
    posts = fetch_horror_posts()
    if not posts:
        print("No qualifying horror posts right now.")
        return 1
    posts.sort(key=score_post, reverse=True)
    rank = int(args[0]) if args else 1
    if not (1 <= rank <= len(posts)):
        print(f"Rank must be 1-{len(posts)}")
        return 1
    pick = posts[rank - 1]
    s = SUB_SHORT.get(pick["sub"], pick["sub"])
    print(f"\nTop 5 horror picks:")
    for i, p in enumerate(posts[:5], 1):
        ss = SUB_SHORT.get(p["sub"], p["sub"])
        print(f"  {i}. [{ss:>8}] [{p['ups']:>4} ups] {p['title'][:80]}")
    print(f"\nBuilding rank {rank}: [{s}] {pick['title']}")
    print(f"Running Claude CLI dialogue separator…")
    script = build_script(pick, use_dialogue_split=True)
    body = script_body_for_preview(script)
    n_voice = sum(1 for ln in body.splitlines() if ln.strip().startswith("[N]"))
    v_voice = sum(1 for ln in body.splitlines() if ln.strip().startswith("[V]"))
    print(f"\nScript: {script}")
    print(f"Speakers: {n_voice} narrator (Bill horror) + {v_voice} victim (Sarah horror)")

    prefix = (
        f"📜 HORROR script ready — approve before render\n"
        f"[{s}] {pick['title']}\n"
        f"Speakers: {n_voice} N + {v_voice} V\n"
        f"To render: horror_pipeline.py render-id {pick['id']}\n\n"
    )
    if send_telegram_text(body, prefix=prefix):
        print("✓ Script sent to Telegram for approval")
    else:
        print("⚠ Telegram send failed — check TELEGRAM_BOT_TOKEN/CHAT_ID")
    return 0


def cmd_render_id(args):
    """Render a previously-built script by Reddit post ID."""
    if not args:
        print("Usage: horror_pipeline.py render-id <post_id>")
        return 1
    pid = args[0]
    script_path = SCRIPT_DIR / f"horror_{pid}.txt"
    if not script_path.exists():
        print(f"Script not found: {script_path}")
        print(f"Run `horror_pipeline.py preview` first.")
        return 1
    print(f"Rendering: {script_path.name}")
    out = render_script(script_path)
    print(f"Rendered: {out}")
    return 0


def cmd_reddit(args):
    """[Legacy] One-shot fetch + render, no approval gate.

    For the approval flow, use `preview` then `render-id` instead.
    """
    posts = fetch_horror_posts()
    if not posts:
        print("No qualifying horror posts right now.")
        return 1
    posts.sort(key=score_post, reverse=True)
    rank = int(args[0]) if args else 1
    pick = posts[rank - 1]
    print(f"\n[no approval gate] rendering: {pick['title']}")
    script = build_script(pick)
    out = render_script(script)
    print(f"Rendered: {out}")
    return 0


COMMANDS = {
    "fetch":     cmd_fetch,
    "list":      cmd_list,
    "render":    cmd_render,
    "render-id": cmd_render_id,
    "preview":   cmd_preview,
    "reddit":    cmd_reddit,
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    sys.exit(COMMANDS[sys.argv[1]](sys.argv[2:]) or 0)
