"""
Finance Agent — spending analysis, Dr. Profit signal tracking, and TNFund monitoring.
No live price feed required — uses vault statements and web search.
"""

import logging
import re
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)
_running = False

AGENTIC_DIR = Path(__file__).parent
VAULT = Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain"
_STATEMENTS_DIR = VAULT / "Money & Markets" / "Credit Cards" / "Coinbase One Card"
_STATEMENT_KEYWORDS = re.compile(
    r"\b(new statement|here is my statement|statement for|here's my statement|"
    r"credit card statement|card statement|monthly statement|billing statement)\b",
    re.I,
)

FINANCE_CONTEXT = """You are PAIS running a finance intelligence task for Taran Singh.

## Who you are helping
- Taranveer Singh, Penn State AI Engineering student (2024-2028)
- Crypto trader following Dr. Profit signals on X/Twitter
- Built TNFund trading bot on Charles Schwab API
- Uses Coinbase One Card (pays with crypto rewards, zero fees/interest, $10K limit)
- Recurring expenses: rent ~$1,045/mo (Meridian, Pittsburgh), DigitalOcean $16.96/mo, FirstEnergy electric (peaks in winter)

## Available data sources

### 1. Vault credit card statements (7+ months of data — RICHEST SOURCE)
Path: {vault}/Money & Markets/Credit Cards/Coinbase One Card/
Statements: Sep 2025 through Mar 2026 (and newer if present)
Use these for spending analysis, trend detection, and budget work.

### 2. Dr. Profit vault notes
Path: {vault}/Money & Markets/ (Dr-Profit-* files)
Contains: all signals, trades, theses, and performance analysis going back to 2021.
Use for signal history, pattern recap, and win-rate review.

### 3. X/Twitter web search (for current signals/news only)
Search: "Dr Profit crypto site:x.com" or "bitcoin ethereum news today"
Use WebSearch — check for recent posts if the task asks about current signals.

### 4. TNFund bot logs
Check: ~/agentic_os/pais.log and ~/agentic_os/server.log for any TNFund entries.
Also look for performance data in the vault: {vault}/Projects & Building/TNFund Trading Bot/

## Task dispatch logic

### "spending" / "spending analysis" / "spending digest"
This is the primary use case. Read ALL available statement files and produce:
- Total spent per month (comparison table)
- Top 5 spending categories (food delivery, travel, entertainment, shopping, subscriptions)
- Largest individual charges this period
- Recurring subscriptions list with monthly cost
- Month-over-month trends (growing/shrinking categories)
- Any anomalies or one-off large charges

### "budget" / "budget review"
Compare actual spending to a reasonable target budget:
- Rent: ~$1,045 (fixed)
- Food delivery (Uber Eats, DoorDash): target <$150/mo
- Groceries/Walmart: target <$200/mo
- Entertainment (concerts, Lineleap, events): variable
- Subscriptions: list all and flag ones to cancel
- DigitalOcean: $16.96 (justified)
Flag any categories significantly over target.

### "dr profit" / "signals" / "signal recap"
Read Dr-Profit vault files and summarize:
- Most recent signals and their outcomes
- Overall win rate (from Dr-Profit-Performance-Analysis.md)
- Any patterns that are currently setting up
- Whether any open positions are signaled

### "tnfund" / "bot status"
Check bot logs and vault for:
- Last known bot status and recent trades
- P&L from performance analysis page
- Any errors or issues in the log files

### "weekly digest" / "full report"
Cover all sources: spending summary + Dr. Profit recap + bot status

### Anything else
Interpret and pick the most relevant sources.

## Output format
Write reports to the vault:
File: {vault}/Money & Markets/Finance Digests/YYYY-MM-DD-HH.md

Template:
---
tags:
  - finance
  - digest
---

# Finance Brief -- [Date]

**Parent:** [[Money & Markets]]

## Spending Summary
[Monthly totals table, category breakdown]

## Top Expenses This Period
[Itemized list of largest charges]

## Recurring Subscriptions
[All recurring charges, monthly cost]

## Dr. Profit / Signals
[What you found -- recent signals, win rate, current setups]

## TNFund Bot
[Status and recent performance if available]

## Action Items
[Flag anything to cancel, adjust, or act on]

Return 3-5 sentence summary with key numbers and any urgent flags.
""".format(vault=str(VAULT))


def is_running() -> bool:
    return _running


# ── Statement detection & processing ─────────────────────────────────────────

def _is_statement_task(task: str) -> bool:
    """Return True if the task appears to contain a new credit card statement."""
    return bool(_STATEMENT_KEYWORDS.search(task))


def _get_existing_statements() -> list[str]:
    """Return sorted list of existing statement filenames (without .md)."""
    if not _STATEMENTS_DIR.exists():
        return []
    files = [
        p.stem for p in _STATEMENTS_DIR.glob("*.md")
        if "Statement" in p.name
    ]
    return sorted(files)


def _read_recent_statements(n: int = 2) -> str:
    """Read the last n statement files for month-over-month context."""
    stmts = _get_existing_statements()
    if not stmts:
        return ""
    recent = stmts[-n:]
    parts = []
    for name in recent:
        path = _STATEMENTS_DIR / f"{name}.md"
        try:
            parts.append(f"### {name}\n{path.read_text(encoding='utf-8')}")
        except Exception:
            pass
    return "\n\n".join(parts)


_STATEMENT_PARSE_CONTEXT = """You are a finance parsing agent. Taran has given you a new Coinbase One Card credit card statement.

Your job:
1. Parse the statement and produce a vault note in EXACTLY the format shown below.
2. After the vault note, output a "---MOM---" separator, then a month-over-month comparison.

## Vault note format (follow exactly):

```
---
tags:
  - money
  - credit-cards
  - spending
  - year-YYYY
---

> **[[Money & Markets]]** > **[[Credit Cards]]** > **[[Coinbase One Card]]**

**Navigation:** ← [[PREV_STATEMENT]] | [[NEXT_STATEMENT]] →

---

# Coinbase One Card — [Start Date]–[End Date], YYYY

**Cycle:** [Full cycle range]
**New Balance:** $X | **Purchases:** $X | **Payments:** -$X (description)
**Credit Limit:** $10,000 | **Available Credit:** $X
**Min Payment Due:** $X | **Due Date:** [Date]
**Fees:** $0 | **Interest:** $0

---

## Transactions
| Date | Description | Category | Amount |
|---|---|---|---|
[one row per transaction]

---

## Highlights
- [3–6 bullet points calling out biggest charges, patterns, recurring expenses]
```

Rules:
- PREV_STATEMENT = most recent existing statement filename (without .md). If this is the newest, leave NEXT_STATEMENT blank and the navigation line becomes: **Navigation:** ← [[PREV_STATEMENT]]
- Categorize each transaction: Dining, Groceries, Food Delivery, Electric, Tech/Hosting, Nightlife, Bar, Clothing/Fitness, Shopping, Rideshare, Gas, Entertainment, Trading Subscription, etc.
- Highlights: always flag electric bills, large one-offs (>$100), recurring subscriptions (DigitalOcean, Whop), any anomalies
- Do NOT hallucinate transactions — only include what appears in the statement text

## Previous statements (for navigation and MOM context):
{recent_statements}

## Statement text from Taran:
{statement_text}

After producing the vault note, output:
---MOM---
Then write a concise month-over-month spending comparison (2–3 paragraphs) comparing this statement to the previous 1–2 months. Include: total purchases delta, top category changes, any new or disappeared recurring charges, trend commentary.
"""


async def _process_new_statement(
    task: str,
    broadcast,
    send_telegram,
    sandbox_dir=None,
) -> str:
    """Parse a new statement, write to vault, update hub, return summary."""
    from tools.llm import run_llm_command

    await broadcast({"type": "finance_activity", "text": "Detected new statement — parsing..."})

    recent_stmts = _read_recent_statements(2)
    existing = _get_existing_statements()
    prev_stmt = existing[-1] if existing else ""

    prompt = _STATEMENT_PARSE_CONTEXT.format(
        recent_statements=recent_stmts or "(none yet)",
        statement_text=task,
    )

    res = await run_llm_command(
        prompt=prompt,
        broadcast=broadcast,
        send_telegram=send_telegram,
        sandbox_dir=sandbox_dir,
        agent_name="finance",
    )

    raw_output = res.get("result", "")
    if not raw_output:
        return "Statement parsing returned empty output."

    # Split vault note from MOM comparison
    if "---MOM---" in raw_output:
        vault_note_raw, mom_text = raw_output.split("---MOM---", 1)
    else:
        vault_note_raw = raw_output
        mom_text = ""

    vault_note = vault_note_raw.strip()

    # Extract the statement title from the H1 line to build filename
    title_match = re.search(r"# Coinbase One Card — (.+)", vault_note)
    if not title_match:
        return "Could not extract statement title from parsed output. Raw:\n" + vault_note[:400]

    statement_title = title_match.group(1).strip()

    # Build filename from cycle line, e.g. "Apr 8–May 7, 2026" → "May 2026 Statement (Apr 8 – May 7)"
    cycle_match = re.search(r"\*\*Cycle:\*\* ([^\n]+)", vault_note)
    if cycle_match:
        cycle_str = cycle_match.group(1).strip()
        # e.g. "Apr 8 – May 7, 2026"
        # Filename convention: "{End Month} {Year} Statement ({Start} – {End no year})"
        # Try to parse it
        date_range_match = re.match(
            r"(\w+ \d+)\s*[–-]\s*(\w+ \d+),?\s*(\d{4})", cycle_str
        )
        if date_range_match:
            start_d = date_range_match.group(1)    # "Apr 8"
            end_d   = date_range_match.group(2)    # "May 7"
            year    = date_range_match.group(3)    # "2026"
            # End month name for the filename prefix
            end_month = end_d.split()[0]           # "May"
            filename = f"{end_month} {year} Statement ({start_d} – {end_d})"
        else:
            filename = f"New Statement {datetime.now().strftime('%Y-%m')}"
    else:
        filename = f"New Statement {datetime.now().strftime('%Y-%m')}"

    note_path = _STATEMENTS_DIR / f"{filename}.md"
    await broadcast({"type": "finance_activity", "text": f"Writing vault note: {filename}.md"})

    try:
        note_path.write_text(vault_note, encoding="utf-8")
    except Exception as e:
        log.exception("Failed to write statement note")
        return f"Error writing vault note: {e}"

    # Update hub: append to Statements list in Coinbase One Card.md
    hub_path = _STATEMENTS_DIR / "Coinbase One Card.md"
    try:
        hub_text = hub_path.read_text(encoding="utf-8")
        link = f"- [[{filename}]]"
        if link not in hub_text:
            hub_text = hub_text.rstrip() + f"\n{link}\n"
            hub_path.write_text(hub_text, encoding="utf-8")
    except Exception as e:
        log.warning("Hub update failed: %s", e)

    # Update index.md
    index_path = VAULT / "index.md"
    try:
        idx = index_path.read_text(encoding="utf-8")
        if filename not in idx:
            # Find the credit cards section and append
            idx = idx.rstrip() + f"\n- [[{filename}]] — Coinbase One Card statement\n"
            index_path.write_text(idx, encoding="utf-8")
    except Exception as e:
        log.warning("Index update failed: %s", e)

    # Write MOM comparison to vault if we have it
    mom_path = None
    if mom_text.strip():
        digests_dir = VAULT / "Money & Markets" / "Finance Digests"
        digests_dir.mkdir(parents=True, exist_ok=True)
        mom_filename = f"{datetime.now().strftime('%Y-%m-%d-%H%M')}-statement-mom.md"
        mom_path = digests_dir / mom_filename
        mom_note = (
            "---\ntags:\n  - finance\n  - spending\n  - month-over-month\n---\n\n"
            f"# Month-over-Month — {filename}\n\n"
            f"**Parent:** [[Money & Markets]]\n\n"
            f"{mom_text.strip()}\n"
        )
        try:
            mom_path.write_text(mom_note, encoding="utf-8")
        except Exception as e:
            log.warning("MOM note write failed: %s", e)
            mom_path = None

    # Append to vault log
    log_path = VAULT / "log.md"
    try:
        log_entry = (
            f"\n## [{datetime.now().strftime('%Y-%m-%d')}] ingest | {filename}\n"
            f"- Parsed and filed new Coinbase One Card statement\n"
            f"- Note: [[{filename}]]\n"
        )
        if mom_path:
            log_entry += f"- MOM analysis: [[{mom_path.stem}]]\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(log_entry)
    except Exception as e:
        log.warning("Log append failed: %s", e)

    summary = f"Statement saved: [[{filename}]]."
    if mom_path:
        summary += f" Month-over-month comparison filed in Finance Digests."
    await send_telegram(summary)
    return summary


def _load_lessons(max_chars: int = 1000) -> str:
    lessons = AGENTIC_DIR / "lessons.md"
    if not lessons.exists() or lessons.stat().st_size == 0:
        return ""
    return "\n\n## Past Lessons\n" + lessons.read_text(encoding="utf-8")[-max_chars:]


async def run_finance_task(task_description: str, broadcast, send_telegram, sandbox_dir=None):
    global _running
    _running = True
    try:
        await broadcast({"type": "finance_activity", "text": f"Starting: {task_description[:80]}"})

        # Route to statement processor if a new statement is being submitted
        if _is_statement_task(task_description):
            result = await _process_new_statement(
                task_description, broadcast, send_telegram, sandbox_dir
            )
            await broadcast({"type": "finance_activity", "text": "Done."})
            return result

        from tools.llm import run_llm_command
        full_prompt = f"{FINANCE_CONTEXT}{_load_lessons()}\n\n## Task\n{task_description}"

        res = await run_llm_command(
            prompt=full_prompt,
            broadcast=broadcast,
            send_telegram=send_telegram,
            sandbox_dir=sandbox_dir,
            agent_name="finance",
        )

        result = res.get("result", "Finance task complete.")
        await broadcast({"type": "finance_activity", "text": "Done."})
        await send_telegram("Finance brief ready. Check the vault.")
        return result

    except Exception as e:
        log.exception("Finance task error")
        await broadcast({"type": "finance_error", "text": str(e)})
        return f"Error: {e}"
    finally:
        _running = False
