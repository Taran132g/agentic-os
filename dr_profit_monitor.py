"""
Dr. Profit Signal Monitor — watches a Telegram channel using a user account (Pyrogram).

Requires one-time setup:
  1. Get API credentials from https://my.telegram.org/apps
  2. Add to .env:  TELEGRAM_API_ID, TELEGRAM_API_HASH, DR_PROFIT_CHANNEL
  3. Run setup:    python3.11 dr_profit_monitor.py --setup
     (enter your phone number and the verification code once)
  4. After setup, a session file (dr_profit_session.session) is created.
     From then on, the monitor starts automatically with PAIS.

Channel formats accepted:
  DR_PROFIT_CHANNEL=@channelname   (public)
  DR_PROFIT_CHANNEL=-1001234567890  (private, use ID)
"""

import asyncio
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

log = logging.getLogger(__name__)

AGENTIC_DIR    = Path(__file__).parent
SESSION_FILE   = AGENTIC_DIR / "dr_profit_session"
VAULT          = Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain"
SIGNALS_LOG    = VAULT / "Money & Markets" / "Dr-Profit-Signals-Live.md"

API_ID      = int(os.environ.get("TELEGRAM_API_ID", "0") or "0")
API_HASH    = os.environ.get("TELEGRAM_API_HASH", "")
CHANNEL     = os.environ.get("DR_PROFIT_CHANNEL", "")
BOT_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID     = int(os.environ.get("TELEGRAM_CHAT_ID", "0") or "0")

_monitor_running = False


# ── Signal parsing ────────────────────────────────────────────────────────────

# Known crypto assets
_ASSETS = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "DOGE", "SHIB",
    "LINK", "DOT", "MATIC", "LTC", "UNI", "ATOM", "NEAR", "APT", "ARB",
    "OP", "SUI", "INJ", "TIA", "SEI", "WIF", "PEPE", "FLOKI",
]

_SIG_KEYWORDS = re.compile(
    r"\b(long|short|buy|sell|entry|enter|signal|trade|position|alert|setup)\b",
    re.I,
)

# Capture an optional k/K suffix INSIDE the group so _clean_price can scale it —
# otherwise "Entry: 3.5k" parsed as 3.5 instead of 3500 (1000× wrong sizing).
_NUM = r"[\d,]+(?:\.\d+)?\s*[kK]?"
_PRICE_PAT    = re.compile(rf"[\$]?({_NUM})")
_ENTRY_PAT    = re.compile(rf"(?:entry|enter|buy(?:\s+at)?|price)[:\s]+[\$]?({_NUM})", re.I)
_SL_PAT       = re.compile(rf"(?:sl|stop.?loss|stop)[:\s]+[\$]?({_NUM})", re.I)
_TP_PAT       = re.compile(rf"(?:tp\d?|take.?profit|target\s*\d*)[:\s]+[\$]?({_NUM})", re.I)
_LEV_PAT      = re.compile(r"(\d+)[xX]\s*(?:leverage|lev)?", re.I)
_RANGE_PAT    = re.compile(rf"[\$]?({_NUM})\s*[-–]\s*[\$]?({_NUM})")


def _clean_price(s: str) -> float:
    """Parse a price string like '65,000', '65k', '65.5' into a float."""
    s = s.replace(",", "").strip()
    if s.lower().endswith("k"):
        return float(s[:-1]) * 1000
    return float(s)


def parse_signal(text: str) -> dict | None:
    """
    Try to extract a structured trade signal from a Telegram message.
    Returns None if the message does not look like a signal.
    """
    text_upper = text.upper()

    # Must contain a signal keyword to qualify
    if not _SIG_KEYWORDS.search(text):
        return None

    # Detect asset — word-boundary match so tickers aren't matched as substrings
    # inside other words ("OP" inside "STOP", "ATOM" inside "ATOMIC", etc.).
    asset = None
    for a in _ASSETS:
        if re.search(rf"\b{re.escape(a)}\b", text_upper):
            asset = a
            break
    if not asset:
        return None

    # Detect direction
    direction = "LONG"
    if re.search(r"\b(short|sell)\b", text, re.I):
        direction = "SHORT"
    elif re.search(r"\b(long|buy)\b", text, re.I):
        direction = "LONG"

    # Entry price
    entry = None
    m = _ENTRY_PAT.search(text)
    if m:
        try:
            entry = _clean_price(m.group(1))
        except ValueError:
            pass
    if entry is None:
        # Try range (take midpoint)
        rm = _RANGE_PAT.search(text)
        if rm:
            try:
                lo, hi = _clean_price(rm.group(1)), _clean_price(rm.group(2))
                entry = round((lo + hi) / 2, 2)
            except ValueError:
                pass

    # Stop loss
    stop_loss = None
    m = _SL_PAT.search(text)
    if m:
        try:
            stop_loss = _clean_price(m.group(1))
        except ValueError:
            pass

    # Take profit targets
    take_profits = []
    for m in _TP_PAT.finditer(text):
        try:
            take_profits.append(_clean_price(m.group(1)))
        except ValueError:
            pass

    # Leverage
    leverage = 1
    m = _LEV_PAT.search(text)
    if m:
        try:
            leverage = min(int(m.group(1)), 50)
        except ValueError:
            pass

    if not entry:
        return None  # Can't size a trade without an entry

    return {
        "asset":       asset,
        "direction":   direction,
        "entry":       entry,
        "stop_loss":   stop_loss,
        "take_profit": take_profits,
        "leverage":    leverage,
        "raw":         text[:600],
    }


# Free-text trade-intent hint. Dr. Profit posts mostly free text the strict regex
# parser misses ("I bought CIRCLE at $65", "#ONDO buying spot here"). If a post
# mentions a trade action we hand the raw text to the executor's LLM parse, which
# does the real filtering via its confidence gate. Pure commentary is skipped so we
# don't fire the LLM on chatter.
_TRADE_HINT = re.compile(
    r"\b(buy|buying|bought|long|longed|short|shorted|sell|selling|sold|entry|enter|"
    r"entered|accumulat\w*|closing|closed|take[\s-]?profit|stop[\s-]?loss|leverage|"
    r"position|spot|grabbed|ape[ds]?|order|target|tp\d?|\bsl\b)\b", re.I)


def _looks_tradeable(text: str) -> bool:
    return bool(_TRADE_HINT.search(text))


# ── Trade brief formatter ─────────────────────────────────────────────────────

def format_trade_brief(sig: dict) -> str:
    """Build the Telegram alert message from a parsed signal + 20% bankroll sizing."""
    from tools.trade_tracker import calculate_sizes

    sz = calculate_sizes(
        entry     = sig["entry"],
        stop_loss = sig.get("stop_loss"),
        asset     = sig["asset"],
        leverage  = sig.get("leverage", 1),
    )

    direction_emoji = "🟢" if sig["direction"] == "LONG" else "🔴"
    lev_str = f" {sig['leverage']}x" if sig["leverage"] > 1 else ""

    lines = [
        "📩 DR PROFIT SIGNAL",
        sig["raw"].strip(),
        "",
        "─" * 30,
        f"{direction_emoji} {sig['asset']} {sig['direction']}{lev_str}",
        "",
        f"Entry:     ${sig['entry']:,.2f}",
    ]

    if sig.get("stop_loss"):
        sl_pct = abs(sig["entry"] - sig["stop_loss"]) / sig["entry"] * 100
        lines.append(f"Stop Loss: ${sig['stop_loss']:,.2f}  ({sl_pct:.1f}% away)")
    else:
        lines.append("Stop Loss: not found (assumed 2%)")

    if sig.get("take_profit"):
        for i, tp in enumerate(sig["take_profit"], 1):
            tp_pct = abs(tp - sig["entry"]) / sig["entry"] * 100
            lines.append(f"TP{i}:       ${tp:,.2f}  (+{tp_pct:.1f}%)")

    lines += [
        "",
        f"Bankroll:  ${sz['bankroll']:,.2f}",
        f"Risk:      20% = ${sz['risk_usd']:.2f}",
        "",
        "Position:",
        f"  {sz['units']} {sig['asset']}  ≈${sz['notional']:,.0f} notional",
    ]

    if sz.get("margin"):
        lines.append(f"  Margin required: ${sz['margin']:,.2f}")

    lines.append("\nTrack this trade on the Trades dashboard.")
    return "\n".join(lines)


# ── Vault logger ──────────────────────────────────────────────────────────────

def _log_signal_to_vault(sig: dict, raw_text: str):
    """Append signal to the live signals log in the vault."""
    try:
        SIGNALS_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = (
            f"\n## [{ts}] {sig['asset']} {sig['direction']}\n"
            f"- Entry: {sig['entry']}\n"
            f"- SL: {sig.get('stop_loss', 'N/A')}\n"
            f"- TPs: {sig.get('take_profit', [])}\n"
            f"- Raw: {raw_text[:300]}\n"
            f"---"
        )
        # Decide whether the file needs a header BEFORE opening it in append
        # mode (which would otherwise create a 0-byte file and confuse the check).
        need_header = not SIGNALS_LOG.exists() or SIGNALS_LOG.stat().st_size == 0
        with open(SIGNALS_LOG, "a", encoding="utf-8") as f:
            if need_header:
                f.write("---\ntags:\n  - signals\n  - dr-profit\n---\n\n# Dr. Profit Live Signals\n\n")
            f.write(entry)
    except Exception as e:
        log.warning("Failed to log signal to vault: %s", e)


# ── Pyrogram monitor ──────────────────────────────────────────────────────────

async def _send_bot_message(text: str):
    """Send a message via the PAIS Telegram bot."""
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": CHAT_ID, "text": text, "parse_mode": ""},
                timeout=10,
            )
    except Exception as e:
        log.warning("Bot send failed: %s", e)


async def start_monitor():
    """
    Start the Pyrogram user-account listener.
    Silently skips if credentials aren't configured.
    """
    global _monitor_running

    if not all([API_ID, API_HASH, CHANNEL]):
        log.info("[dr_profit] Credentials not set — monitor disabled. Run --setup to configure.")
        return

    if not SESSION_FILE.with_suffix(".session").exists():
        log.info("[dr_profit] No session file — run: python3.11 dr_profit_monitor.py --setup")
        return

    try:
        from pyrogram import Client
        from pyrogram.types import Message
    except ImportError:
        log.warning("[dr_profit] pyrogram not installed: pip install pyrogram tgcrypto")
        return

    _monitor_running = True
    log.info("[dr_profit] Starting signal monitor for channel: %s", CHANNEL)

    app = Client(
        str(SESSION_FILE),
        api_id=API_ID,
        api_hash=API_HASH,
    )

    # Parse channel target — int or string
    try:
        channel_target = int(CHANNEL)
    except ValueError:
        channel_target = CHANNEL

    @app.on_message()
    async def on_message(client: Client, message: Message):
        # Manual channel match instead of filters.chat() — the built-in chat filter
        # can silently miss channel updates on a fresh session. The diagnostic line
        # confirms whether updates are arriving at all and from which chats.
        chat = message.chat
        cid = chat.id if chat else None
        if cid != channel_target:
            log.info("[dr_profit] (ignored) update from chat %s", cid)
            return

        raw = message.text or message.caption or ""
        if not raw.strip():
            return

        log.info("[dr_profit] New message from target channel (%d chars)", len(raw))

        sig = parse_signal(raw)   # strict regex — often None on his free-text posts

        # Route to the executor when the regex found a structured signal OR the post
        # reads like a trade action. The executor's LLM parse handles free text and its
        # confidence gate filters chatter. Pure commentary is dropped here.
        if sig is None and not _looks_tradeable(raw):
            return

        verdict_block = ""
        if sig is not None:
            log.info("[dr_profit] Signal detected: %s %s @ %s",
                     sig["asset"], sig["direction"], sig["entry"])
            _log_signal_to_vault(sig, raw)
            # Advisory risk gate only when we have a structured signal to score.
            try:
                from risk_gate_workflow import evaluate_signal, format_verdict_block
                verdict = await evaluate_signal(sig)
                verdict_block = format_verdict_block(verdict)
                log.info("[dr_profit] Risk gate verdict: %s (conf %.0f)",
                         verdict["verdict"], verdict["confidence"])
            except Exception as e:
                log.warning("[dr_profit] Risk gate skipped: %s", e)
        else:
            log.info("[dr_profit] Free-text trade post — handing to executor's LLM parse")

        # Executor: agent parse + risk gate + caps + (paper/real) placement. Fail-safe.
        exec_block, exec_res = "", {}
        try:
            from execution_workflow import execute_signal, format_exec_block
            exec_res = await execute_signal(raw, source="dr_profit")
            disarmed = (exec_res.get("status") == "blocked"
                        and "auto-execute is OFF" in (exec_res.get("reason") or ""))
            if not disarmed:
                exec_block = format_exec_block(exec_res)
            log.info("[dr_profit] executor: %s — %s", exec_res.get("status"),
                     exec_res.get("reason") or f"{exec_res.get('mode')} placed")
        except Exception as e:
            log.warning("[dr_profit] executor error (alert still sent): %s", e)

        # Alert body: structured brief when the regex parsed a signal, else the raw post.
        brief = format_trade_brief(sig) if sig is not None else \
            "📩 DR PROFIT (free-text)\n" + raw.strip()[:400]

        # For free-text posts, only alert when the executor actually acted (placed/
        # failed) so chatter that the LLM filters out stays silent. Regex signals
        # always alert (existing behaviour).
        meaningful = (sig is not None or exec_res.get("executed")
                      or exec_res.get("status") == "failed")
        if meaningful:
            await _send_bot_message(brief + verdict_block + exec_block)

    try:
        await app.start()
        # Prime the peer cache. A fresh Pyrogram login silently drops updates from
        # channels it hasn't "seen" yet — iterating dialogs once caches the peer so
        # incoming channel messages actually reach the on_message handler.
        try:
            found = False
            async for d in app.get_dialogs():
                if d.chat and d.chat.id == channel_target:
                    found = True
            log.info("[dr_profit] Peer cache primed (target channel in dialogs: %s)", found)
        except Exception as e:
            log.warning("[dr_profit] dialog prime failed: %s", e)
        log.info("[dr_profit] Monitor running.")
        # Keep alive until cancelled
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        try:
            await app.stop()
        except Exception:
            pass
        _monitor_running = False
        log.info("[dr_profit] Monitor stopped.")


def is_monitor_running() -> bool:
    return _monitor_running


# ── One-time setup CLI ────────────────────────────────────────────────────────

def setup():
    """Interactive first-time authentication. Run once."""
    print("\n=== Dr. Profit Monitor Setup ===\n")
    print("NOTE: This authenticates as YOUR personal Telegram account so it can")
    print("read the Dr. Profit channel you're subscribed to. Enter your phone")
    print("number (e.g. +12155551234) when prompted — NOT a bot token.\n")

    # Use existing .env values as defaults
    env_path = Path(__file__).parent / ".env"
    env_text = env_path.read_text()

    def _get_env(key):
        m = re.search(rf"^{key}=(.+)$", env_text, re.M)
        return m.group(1).strip() if m else ""

    default_id   = _get_env("TELEGRAM_API_ID")
    default_hash = _get_env("TELEGRAM_API_HASH")
    default_ch   = _get_env("DR_PROFIT_CHANNEL")

    prompt_id   = f"Telegram API ID [{default_id}]: " if default_id else "Telegram API ID: "
    prompt_hash = f"Telegram API hash [{default_hash}]: " if default_hash else "Telegram API hash: "
    prompt_ch   = f"Dr. Profit channel ID [{default_ch}]: " if default_ch else "Dr. Profit channel ID: "

    api_id   = input(prompt_id).strip()   or default_id
    api_hash = input(prompt_hash).strip() or default_hash
    channel  = input(prompt_ch).strip()   or default_ch

    # Write to .env
    for key, val in [("TELEGRAM_API_ID", api_id), ("TELEGRAM_API_HASH", api_hash), ("DR_PROFIT_CHANNEL", channel)]:
        if f"{key}=" in env_text:
            env_text = re.sub(rf"^{key}=.*$", f"{key}={val}", env_text, flags=re.M)
        else:
            env_text += f"\n{key}={val}"
    env_path.write_text(env_text)
    print("Saved credentials to .env")

    # Authenticate as user account
    print("\nStep 2: Authenticating with YOUR personal Telegram account...")
    print("(You will be asked for your phone number and a verification code)\n")
    from pyrogram import Client
    app = Client(str(SESSION_FILE), api_id=int(api_id), api_hash=api_hash)

    async def _auth():
        await app.start()
        me = await app.get_me()
        print(f"\nAuthenticated as: {me.first_name} (@{me.username})")
        print(f"Session saved to: {SESSION_FILE}.session")
        await app.stop()

    try:
        asyncio.run(_auth())
    except RuntimeError:
        # Pyrogram 2.0 asyncio cleanup bug — session is already saved, safe to ignore
        pass
    print("\nSetup complete! Restart PAIS and the monitor will start automatically.")


if __name__ == "__main__":
    if "--setup" in sys.argv:
        setup()
    else:
        asyncio.run(start_monitor())
