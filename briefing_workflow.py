"""
Briefing Agent — two modes:
  - Daily review: yesterday's vault activity + today's focus + markets + trading
  - Weekly review: last week recap + next week plan + markets + trading
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)
_running = False

AGENTIC_DIR   = Path(__file__).parent
VAULT         = Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain"
ACTIVITY_FILE = AGENTIC_DIR / "activity.json"
TRADES_FILE   = AGENTIC_DIR / "trades.json"

# Vault path as a shell-safe string for embedding in Bash instructions
_VAULT_BASH = '$HOME/Library/Mobile\\ Documents/iCloud~md~obsidian/Documents/Digital\\ Brain'

DAILY_CONTEXT = """You are PAIS creating Taran's daily review.

## Who you are briefing
- Taranveer Singh, Penn State AI Engineering student (2024-2028)
- Summer 2026, home in Royersford PA
- Active projects: PAIS (Agentic OS), FindingFounders, Content Pipeline, Trading

## Step 1 — Gather inputs FIRST (Bash; do not write anything yet)

```bash
VAULT="$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain"
PAIS="$HOME/agentic_os"

# ── Shared signals
tail -300 "$VAULT/log.md"
cat "$PAIS/activity.json"
cat "$PAIS/trades.json"

# ── All vault files modified in last 36 hours
find "$VAULT" -name "*.md" -mtime -2 -not -path "*/.obsidian/*" 2>/dev/null | sort

# ── Per-bucket reads (use these to scope each section)
# 1. PAIS
ls -lt "$VAULT/PAIS Hub/Tasks/" 2>/dev/null | head -15
cat "$VAULT/Chats/$(date -v-1d +%F).md" 2>/dev/null || true
cat "$VAULT/Chats/$(date +%F).md" 2>/dev/null || true
cat "$VAULT/Projects & Building/Agentic OS.md" 2>/dev/null | head -60
find "$PAIS" -maxdepth 2 -name "*.py" -mtime -2 2>/dev/null | head -20

# 2. FindingFounders
ls -lt "$VAULT/Projects & Building/" 2>/dev/null | grep -i finding
for f in "$VAULT/Projects & Building/"FindingFounders*.md; do
  [ -f "$f" ] && echo "── $f" && head -40 "$f"
done

# 3. Content Pipeline
cat "$VAULT/Projects & Building/Content Pipeline.md" 2>/dev/null | head -60
cat "$VAULT/Projects & Building/Podcast Strategy.md" 2>/dev/null | head -40
ls -lt "$HOME/Desktop"/*.mp4 2>/dev/null | head -5
stat -f "%Sm %N" "$PAIS/content_pipeline.py" 2>/dev/null

# 4. Trading (extra context)
cat "$VAULT/Money & Markets/Dr-Profit-Trades-2026.md" 2>/dev/null | tail -80
```

Read every file from the recently-modified list. Match each file to ONE bucket
below (PAIS / FindingFounders / Content / Trading / Other). Anything that
doesn't fit the first four goes to Other.

## Step 2 — Verify markets BEFORE writing

Run at least TWO separate WebSearch queries per number:
- "bitcoin price today" + "BTC price coinmarketcap"
- "ethereum price today" + "ETH coingecko"
- "crypto fear greed index today"
If two sources disagree by >2%, write a range (e.g. "$76k-$79k").
Never quote a price from memory or a single source.

## Step 3 — Write the brief with this exact structure

File path: `$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain/Briefings/YYYY-MM-DD Daily Brief.md`

```
---
tags:
  - briefing
  - daily
date: YYYY-MM-DD
---

# Daily Brief — Weekday Mon DD, YYYY

> Generated HH:MM.

## 1. PAIS (Agentic OS)
**Yesterday:**
- [Specific completed items from activity.json + Chats/<yesterday>.md + PAIS Hub/Tasks/*]
- [Code changes — name the file:line if visible from git status or mtime]
- [Architectural decisions made]

**Next:** [ONE concrete next step, named file/feature/decision]

## 2. FindingFounders
**Yesterday:** [bullets from FindingFounders *.md edits + log entries tagged findingfounders]
**Next:** [next phase deliverable — name the doc or feature]

## 3. Content Pipeline
**Yesterday:** [content_pipeline.py edits, new MP4s on Desktop, podcast strategy notes, scripts written]
**Next:** [next reel topic or podcast episode]
**📜 AITA daily reminder:** Run the AITA workflow today — `/aita` in Telegram, or tap **🎬 Run AITA Workflow** on the Content agent page. Pick a hook, review the spell-checked script, render and post.

## 4. Trading & Markets
**Live (verified, 2 sources):**
- BTC: $X (source A: $X, source B: $X)
- ETH: $X
- Fear & Greed: N (Extreme Fear / Fear / Neutral / Greed / Extreme Greed)

**Open positions (from trades.json):**
- Asset Direction | Entry: $X | SL: $X | TP: $X | Status: X
  → Unrealised PnL: $±X (using live BTC/ETH above)
  → Stop loss at risk? Yes/No (proximity to SL)

**Dr. Profit signals (last 24h):** [any new from Dr-Profit-Trades-2026.md, or "none"]

## 5. Other
[Catch-all — anything modified in last 36h not in buckets 1-4: chats, vault
maintenance, study, money, personal. If nothing, say "Nothing else of note."]

## 6. Today's Top 3
The 3 highest-leverage moves across all five buckets above. Be specific:
1. [Project] — [concrete action with file/feature/contact named]
2. [Project] — [concrete action]
3. [Project] — [concrete action]

Rank by impact, not chronology. Name the specific file, feature, contact, or
decision — "Wire Supabase project URL into FindingFounders frontend/.env.local"
beats "set up keys".
```

## Telegram return
Send the **full brief** to Telegram so Taran can read it like a morning
newspaper on his phone. Send the same markdown body you wrote to the vault
file — sections 1 through 6 plus the Related block — preserving headings and
bullets. Do NOT summarize, do NOT just send "brief is ready", do NOT only send
Top 3. The vault file and the Telegram message must contain the same content.

Telegram caps messages at 4096 characters, so split on section boundaries
(`## 1.`, `## 2.`, …) and send each section as its own message, in order.
Never split mid-section if the section fits; only split a single oversized
section across multiple messages if it exceeds the cap on its own.

## Hard rules
- If a bucket had no activity yesterday, write "No activity yesterday." for Yesterday and still give a Next.
- Today's Top 3 must be **actionable today** — no "decide on X strategy" without a specific decision/output.
- Do not invent activity. If activity.json or vault is empty for a bucket, say so.
"""

WEEKLY_CONTEXT = """You are PAIS creating Taran's weekly review.

## Who you are briefing
- Taranveer Singh, Penn State AI Engineering student (2024-2028)
- DeFi Club Trading & Technology Lead at Penn State
- Building FindingFounders and TNFund trading bot
- Follows AI/ML closely, trades crypto, watches Penn State news

## Step 1 — Read the vault (do this FIRST with Bash before writing anything)

Run these Bash commands to gather context:

```bash
VAULT="$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain"

# 1. ALL files modified in the last 7 days
find "$VAULT" -name "*.md" -mtime -7 -not -path "*/.obsidian/*" 2>/dev/null | sort

# 2. Full vault log (last 30 entries or all entries from this week)
tail -500 "$VAULT/log.md"

# 3. PAIS activity log (full — all 7 days)
cat "$HOME/agentic_os/activity.json"

# 4. Current trades
cat "$HOME/agentic_os/trades.json"

# 5. Key project notes — read each one:
cat "$VAULT/Projects & Building/Agentic OS.md"
cat "$VAULT/Money & Markets/Dr-Profit-Trades-2026.md" 2>/dev/null || true

# 6. For any recently modified note found in step 1, read its content too
```

Read EVERY file from step 1. These are the notes that were actually touched this week — they are the primary source of truth for what was worked on.

## Step 2 — Cover ALL five sections

### 1. Last Week's Progress
Based on vault files modified this week + vault log + activity log:
- List major milestones by project (PAIS, FindingFounders, Content Pipeline, TNFund)
- Reference specific note names, features built, or decisions made
- Include things Taran worked on directly in Obsidian, not just PAIS agent output
- Be concrete — "built X", "wrote Y note", "decided Z"

### 2. Next Week's Plan
Based on open threads across all projects:
- 3–5 specific deliverables with project name
- Order by priority/impact

### 3. Financial Markets
Run at least TWO separate WebSearch queries to verify any price:
- "bitcoin price today" + "BTC weekly performance [date range]"
- "ethereum price today" + "ETH this week"
- "S&P 500 this week performance"
- "crypto fear greed index"
- "crypto market week of [date]" for overall sentiment
If sources disagree by more than 2%, write a range.
Never state a price from memory or a single source.

### 4. Trading Review
From trades.json (Step 1):
- Open positions: entry zone, SL, TP, status, unrealised PnL (use live price from section 3)
- Closed trades this week: PnL
- Bankroll snapshot: starting vs current
- Any Dr. Profit signals from this week (check Dr-Profit-Trades-2026.md)

### 5. AI/ML & Ecosystem News
Use WebSearch:
- "AI news this week [date]"
- "OpenAI Anthropic Google AI announcements this week"
- "crypto DeFi news this week"
- "AI startup landscape" or queries relevant to FindingFounders / current builds
Summarise the 3–5 most relevant developments for Taran.

## Output
Write the full brief to the vault:
File: $HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain/Briefings/YYYY-MM-DD Weekly Brief.md

```
---
tags:
  - briefing
  - weekly
---

# Weekly Brief — [Date range, e.g. May 11–17 2026]

> Generated [time].

## Last Week's Progress
[Project-by-project: specific notes/features/decisions, not vague summaries]

## Next Week's Plan
[3–5 specific deliverables with project name]

## Markets
[BTC price + weekly %, ETH price + weekly %, S&P direction, fear/greed index, sentiment]

## Trading
[Open positions + unrealised PnL, bankroll snapshot, any signals/trades this week]

## AI/ML & Ecosystem
[3–5 bullet points — most relevant developments for Taran's work]

## Action Items
[Things Taran must decide or act on this week]
```

Return a 3-sentence Telegram summary: last week highlight, top priority next week, one-line market/trade status.
"""


def is_running() -> bool:
    return _running


def _load_lessons(max_chars: int = 1000) -> str:
    lessons = AGENTIC_DIR / "lessons.md"
    if not lessons.exists() or lessons.stat().st_size == 0:
        return ""
    return "\n\n## Past Lessons\n" + lessons.read_text(encoding="utf-8")[-max_chars:]


def _load_activity(hours: int = 30) -> str:
    """Load activity.json — passed as pre-context so the agent doesn't have to."""
    if not ACTIVITY_FILE.exists():
        return "No activity log found."
    try:
        entries = json.loads(ACTIVITY_FILE.read_text())
    except Exception:
        return "Could not read activity log."

    cutoff = datetime.now() - timedelta(hours=hours)
    recent = []
    for e in entries:
        try:
            ts = datetime.fromisoformat(e.get("ts", ""))
            if ts >= cutoff:
                agent = e.get("agent", "?")
                task  = e.get("task", "?")[:200]
                recent.append(f"- [{agent}] {task}")
        except Exception:
            continue

    return "\n".join(recent) if recent else f"No tasks completed in the past {hours} hours."


def _load_trades_summary() -> str:
    if not TRADES_FILE.exists():
        return "No trades file found."
    try:
        data = json.loads(TRADES_FILE.read_text())
    except Exception:
        return "Could not read trades file."

    # `.get(k) or 0` treats both missing keys AND None values as 0 — dict.get
    # only substitutes the default when the key is missing, not when the value
    # is explicitly None, which is common for unresolved trade fields.
    bankroll  = data.get("bankroll") or 0
    starting  = data.get("starting_bankroll") or bankroll
    active    = data.get("active_trades", []) or []
    closed    = data.get("closed_trades", []) or []
    pnl_total = sum((t.get("pnl") or 0) for t in closed)

    lines = [
        f"Bankroll: ${bankroll:,.2f} (started ${starting:,.2f}, total closed PnL ${pnl_total:+.2f})",
        f"Open trades: {len(active)} | Closed trades: {len(closed)}",
    ]
    for t in active:
        tps_list  = [p for p in (t.get("take_profit") or []) if p is not None]
        tps       = ", ".join(f"${p:,.0f}" for p in tps_list) or "—"
        entry     = t.get("entry_price")   or 0
        sl        = t.get("stop_loss")     or 0
        size      = t.get("position_size") or 0
        notional  = t.get("notional")      or 0
        risk_usd  = t.get("risk_usd")      or 0
        risk_pct  = t.get("risk_pct")      or 0
        lines.append(
            f"  • {t.get('asset','?')} {t.get('direction','?')} | Entry zone ${entry:,.0f} "
            f"| SL ${sl:,.0f} | TP {tps} "
            f"| Size {size} ({notional:.0f} USD) "
            f"| Risk ${risk_usd:.0f} ({risk_pct:.1f}%) "
            f"| Status: {t.get('status','?')} | Source: {t.get('source','?')}"
        )
        if t.get("signal_text"):
            lines.append(f"    Signal: {t['signal_text'][:200]}")

    return "\n".join(lines)


def _is_daily(task_description: str) -> bool:
    kw = task_description.lower()
    return any(w in kw for w in ("daily", "morning", "yesterday", "today's focus", "9am", "scheduled daily", "daily review"))


async def run_briefing_task(task_description: str, broadcast, send_telegram, sandbox_dir=None):
    global _running
    _running = True
    try:
        now = datetime.now().strftime("%A %B %d, %Y at %I:%M %p")

        if _is_daily(task_description):
            await broadcast({"type": "briefing_activity", "text": f"Generating daily review for {now}..."})
            # Pre-load what we can from the PAIS process (activity + trades)
            # Vault reading is delegated to the LLM agent via Bash (it has full disk access)
            pre_context = (
                f"\n\n## Pre-loaded: PAIS Activity Log (last 30 hrs)\n{_load_activity(hours=30)}"
                f"\n\n## Pre-loaded: Current Trades\n{_load_trades_summary()}"
            )
            context    = DAILY_CONTEXT + pre_context
            brief_type = "daily"
        else:
            await broadcast({"type": "briefing_activity", "text": f"Generating weekly review for {now}..."})
            pre_context = (
                f"\n\n## Pre-loaded: PAIS Activity Log (last 7 days)\n{_load_activity(hours=168)}"
                f"\n\n## Pre-loaded: Current Trades\n{_load_trades_summary()}"
            )
            context    = WEEKLY_CONTEXT + pre_context
            brief_type = "weekly"

        from tools.llm import run_llm_command
        full_prompt = (
            f"{context}{_load_lessons()}\n\n"
            f"## Task\nGenerate this {brief_type} review.\n"
            f"Current date/time: {now}\n\n"
            f"Additional context: {task_description}"
        )

        res = await run_llm_command(
            prompt=full_prompt,
            broadcast=broadcast,
            send_telegram=send_telegram,
            sandbox_dir=sandbox_dir,
            agent_name="briefing",
        )

        result = res.get("result", "Review complete.")
        await broadcast({"type": "briefing_activity", "text": "Review ready."})

        # For daily: the agent has already sent the full brief to Telegram in
        # section-sized chunks per DAILY_CONTEXT. Do not send a wrapper
        # notification — it would arrive after the brief and break the
        # newspaper feel. Weekly keeps the short "ready in your vault" ping
        # since the weekly brief is too long to stream to phone comfortably.
        if brief_type == "weekly":
            await send_telegram("Weekly review is ready in your vault.")

        return result

    except Exception as e:
        log.exception("Briefing task error")
        await broadcast({"type": "briefing_error", "text": str(e)})
        return f"Error: {e}"
    finally:
        _running = False
