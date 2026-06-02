"""
Telegram bot — two responsibilities:
1. /task <description>  → submits a task to the orchestrator queue
2. Approve/Deny buttons → resolves pending approval gates
"""

import asyncio
import logging
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import tools.approval as approval_gate
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, PAIS_URL

log = logging.getLogger(__name__)

# Shared queue — orchestrator worker reads from this
task_queue: asyncio.Queue[str] = asyncio.Queue()

# Will hold the Application instance once started
_app: Application | None = None


def _guard(update: Update) -> bool:
    """Reject messages from anyone other than Taran."""
    return update.effective_chat.id == TELEGRAM_CHAT_ID


async def cmd_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _guard(update):
        return
    task = " ".join(context.args)
    if not task:
        await update.message.reply_text("Usage: /task <description>")
        return
    await task_queue.put(task)
    await update.message.reply_text(f"Task queued: {task}")


async def cmd_fill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fire a job-application fill through n8n.

    /fill <job url>
    /fill Company | Role | <job url>

    POSTs the text to the local n8n 'jobfill' webhook, which runs
    jobfill_cli.py → browser_fill (fire-and-verify). The verify screenshot
    comes back to this chat ~90s later via browser_fill's own Telegram send.
    """
    if not _guard(update):
        return
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text(
            "Usage:\n/fill <job url>\n/fill Company | Role | <job url>")
        return

    import os as _os, json as _json, urllib.request as _u
    hook = _os.environ.get("N8N_JOBFILL_WEBHOOK",
                           "http://localhost:5678/webhook/jobfill")
    payload = _json.dumps({"text": text}).encode()

    def _post() -> int:
        req = _u.Request(hook, data=payload,
                         headers={"Content-Type": "application/json"})
        with _u.urlopen(req, timeout=10) as r:
            return r.status

    try:
        await asyncio.to_thread(_post)
        await update.message.reply_text(
            f"🚀 Firing job-app fill via n8n…\n{text}\n\n"
            f"Verify screenshot lands here in ~90s. Then review the Chrome tab, "
            f"fix anything, upload the résumé, and submit.")
    except Exception as e:
        await update.message.reply_text(
            f"⚠️ Couldn't reach the n8n webhook ({hook}).\n{e}\n\n"
            f"Is n8n running and the 'jobfill' workflow set to Active?")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _guard(update):
        return
    pending = approval_gate.pending_ids()
    qsize = task_queue.qsize()
    msg = f"Queue: {qsize} task(s) waiting\nPending approvals: {len(pending)}"
    if pending:
        msg += f"\nIDs: {', '.join(pending)}"
    await update.message.reply_text(msg)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _guard(update):
        return
    await update.message.reply_text(
        "/task <description> — queue a new task\n"
        "/size <signal text> — calculate position size (20% bankroll risk)\n"
        "/aita — fetch today's top 5 AITA hooks and pick one to render\n"
        "/status — show queue and pending approvals\n"
        "/help — this message"
    )


async def cmd_aita(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetch today's AITA picks on demand and send the picker."""
    if not _guard(update):
        return
    import aita_pipeline
    try:
        picks = await asyncio.to_thread(aita_pipeline.fetch_top_posts)
    except Exception as e:
        await update.message.reply_text(f"AITA fetch failed: {e}")
        return
    if not picks:
        await update.message.reply_text("No qualifying AITA posts right now. Try again later.")
        return
    picks.sort(key=aita_pipeline.score_hook, reverse=True)
    top5 = picks[:5]
    aita_pipeline.save_picks_for_today(top5)
    await send_aita_picks(top5)


def _format_size_reply(signal_text: str) -> str:
    """Parse a Dr. Profit signal and return 20% bankroll risk position sizing."""
    from dr_profit_monitor import parse_signal
    from tools.trade_tracker import calculate_sizes

    sig = parse_signal(signal_text)
    if sig is None:
        return (
            "Could not parse a trade signal from that text.\n\n"
            "Expected: asset name (BTC/ETH/SOL etc), direction (long/short/buy/sell), "
            "and entry price."
        )

    asset     = sig["asset"]
    direction = sig["direction"]
    entry     = sig["entry"]
    stop_loss = sig.get("stop_loss")
    tps       = sig.get("take_profit", [])
    leverage  = sig.get("leverage", 1)

    sz = calculate_sizes(entry=entry, stop_loss=stop_loss, asset=asset, leverage=leverage)
    sl_distance = abs(entry - stop_loss) if stop_loss else entry * 0.02

    direction_emoji = "🟢" if direction == "LONG" else "🔴"
    lev_str = f" {leverage}x" if leverage > 1 else ""

    lines = [
        f"{direction_emoji} *{asset} {direction}{lev_str}*",
        "",
        f"Entry:     ${entry:,.2f}",
    ]

    if stop_loss:
        sl_pct = sl_distance / entry * 100
        lines.append(f"Stop Loss: ${stop_loss:,.2f}  ({sl_pct:.1f}% away)")
    else:
        lines.append("Stop Loss: (assumed 2% — not stated)")

    if tps:
        for i, tp in enumerate(tps, 1):
            tp_pct = abs(tp - entry) / entry * 100
            rr = round(abs(tp - entry) / sl_distance, 2) if sl_distance else 0
            lines.append(f"TP{i}:       ${tp:,.2f}  (+{tp_pct:.1f}%)  R:R 1:{rr}")

    lines += [
        "",
        f"*Bankroll: ${sz['bankroll']:,.2f}*",
        f"*Risk: 20% = ${sz['risk_usd']:.2f}*",
        "",
        "Position sizing:",
        f"  Units:    {sz['units']} {asset}",
        f"  Notional: ~${sz['notional']:,.0f} at entry",
    ]

    if sz.get("margin"):
        lines.append(f"  Margin:   ~${sz['margin']:,.0f} required")

    return "\n".join(lines)


async def cmd_size(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _guard(update):
        return
    signal_text = " ".join(context.args)
    if not signal_text:
        await update.message.reply_text(
            "Usage: /size <signal text>\n\n"
            "Example: /size BTC Long Entry 65000 SL 63500 TP1 68000"
        )
        return
    reply = _format_size_reply(signal_text)
    await update.message.reply_text(reply, parse_mode="Markdown")


async def handle_approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    # Guard FIRST — don't even acknowledge callbacks from non-owner chats
    if update.effective_chat.id != TELEGRAM_CHAT_ID:
        return

    await query.answer()

    data = query.data  # e.g. "approve:abc12345", "deny:abc12345", "route_career:abc12345"

    if data.startswith("aita_like:"):
        await _handle_aita_toggle(query, data, kind="like")
        return

    if data.startswith("aita_dislike:"):
        await _handle_aita_toggle(query, data, kind="dislike")
        return

    if data.startswith("aita_done:"):
        await _handle_aita_done(query, data)
        return

    if data.startswith("aita_skip:"):
        await _handle_aita_skip(query, data)
        return

    if data.startswith("aita_reset:"):
        await _handle_aita_reset(query, data)
        return

    if data.startswith("aita_render:"):
        await _handle_aita_render(query, data)
        return

    if data.startswith("aita_cancel:"):
        await _handle_aita_cancel(query, data)
        return

    if data.startswith("route_"):
        route_choice, action_id = data[len("route_"):].split(":", 1)
        resolved = approval_gate.resolve(action_id, route_choice)
        if resolved:
            status = f"Routed to {route_choice.title()} 🚀"
            await query.edit_message_text(
                text=query.message.text + f"\n\n{status}",
                parse_mode="Markdown",
            )
        else:
            await query.edit_message_text(
                text=query.message.text + "\n\n(Already resolved or timed out)",
                parse_mode="Markdown",
            )
        return

    parts = data.split(":", 1)
    if len(parts) != 2:
        return

    action, action_id = parts
    status = "approved" if action == "approve" else "denied"
    resolved = approval_gate.resolve(action_id, status)

    if resolved:
        status_text = "Approved ✅" if action == "approve" else "Denied ❌"
        await query.edit_message_text(
            text=query.message.text + f"\n\n{status_text}",
            parse_mode="Markdown",
        )
    else:
        await query.edit_message_text(
            text=query.message.text + "\n\n(Already resolved or timed out)",
            parse_mode="Markdown",
        )


# Pending AITA scripts awaiting approval — keyed by short action_id
# value: {"post_id": str, "title": str, "script_path": str, "script_paths": list[str]}
_aita_pending: dict[str, dict] = {}

# Serial render queue — clicks ack instantly and enqueue here. A single
# background worker processes one render job at a time (avoids bg_music.mp3
# collisions and the PTB "Query is too old" bug from blocking the handler).
_render_queue: "asyncio.Queue | None" = None
_render_worker_task: "asyncio.Task | None" = None


def _ensure_render_worker():
    """Idempotently start the render worker on first use."""
    global _render_queue, _render_worker_task
    if _render_queue is None:
        _render_queue = asyncio.Queue()
    if _render_worker_task is None or _render_worker_task.done():
        _render_worker_task = asyncio.create_task(_render_worker_loop())


async def _render_worker_loop():
    """Consume render jobs serially. Each job is the full multi-part render for one pick."""
    import aita_pipeline
    from pathlib import Path
    while True:
        job = await _render_queue.get()
        try:
            script_paths = [Path(p) for p in job["script_paths"]]
            title = job["title"]
            n_parts = len(script_paths)

            for idx, sp in enumerate(script_paths, 1):
                try:
                    out_path = await asyncio.to_thread(aita_pipeline.render_script, sp)
                except Exception as e:
                    log.exception("AITA render failed for %s", sp)
                    await _app.bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID,
                        text=f"⚠️ AITA render failed for '{title}' (part {idx}/{n_parts}): {e}",
                    )
                    break

                # Renders now live on the PAIS Content page — send a light
                # text notification instead of uploading the full video here.
                if n_parts == 1:
                    msg = (
                        f"✅ AITA render ready\n{title}\n\n"
                        f"📂 View it & post to TikTok from PAIS:\n{PAIS_URL}/content"
                    )
                else:
                    msg = (
                        f"✅ AITA render — Part {idx}/{n_parts}\n{title}\n\n"
                        f"📂 View it & post to TikTok from PAIS:\n{PAIS_URL}/content"
                    )
                try:
                    await _app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)
                except Exception:
                    log.exception("AITA render notify failed for %s", out_path)
        except Exception:
            log.exception("Render worker job crashed")
        finally:
            _render_queue.task_done()


def _new_aita_id() -> str:
    import uuid
    return uuid.uuid4().hex[:8]


async def _send_aita_preview(post: dict):
    """Build a script for one liked post and send a preview message with Render/Cancel.

    Long posts produce two scripts (Part 1 + Part 2). The preview shows Part 1's body
    and notes that Part 2 will be queued; a single Render triggers both renders.
    """
    import aita_pipeline
    title = post["title"]
    try:
        script_paths = await asyncio.to_thread(aita_pipeline.build_script, post)
    except Exception as e:
        log.exception("AITA build_script failed")
        await _app.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"⚠️ Script build failed for '{title}': {e}",
        )
        return

    # Normalize — build_script returns list[Path], may be 1 or 2 elements
    if not isinstance(script_paths, list):
        script_paths = [script_paths]
    n_parts = len(script_paths)
    primary_path = script_paths[0]

    raw = primary_path.read_text()
    body_only = raw.split("---", 2)[-1].strip() if raw.startswith("---") else raw

    action_id = _new_aita_id()
    _aita_pending[action_id] = {
        "post_id": post["id"],
        "title": title,
        "script_path": str(primary_path),
        "script_paths": [str(p) for p in script_paths],
    }

    render_label = "Render ✅" if n_parts == 1 else f"Render {n_parts} parts ✅"
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(render_label, callback_data=f"aita_render:{action_id}"),
            InlineKeyboardButton("Cancel ❌", callback_data=f"aita_cancel:{action_id}"),
        ],
    ])

    preview = body_only
    if len(preview) > 3500:
        preview = preview[:3450].rstrip() + "\n…(truncated — full script on disk)"

    parts_note = ""
    if n_parts > 1:
        parts_note = f"\n📺 *Multi-part:* Part 1 shown below. Part 2 will render after.\n"
        eta = f"~{n_parts * 4} min"
    else:
        eta = "~4 min"

    text = (
        f"📜 *Script ready*\n"
        f"_{title}_\n"
        f"{parts_note}\n"
        f"```\n{preview}\n```\n"
        f"_Edit on disk if needed:_\n`{primary_path}`\n\n"
        f"Tap *Render* to start the {eta} render, or *Cancel*."
    )

    try:
        await _app.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID, text=text,
            reply_markup=keyboard, parse_mode="Markdown",
        )
    except Exception:
        await _app.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"Script ready: {title}\n\n{preview}\n\nFile: {primary_path}",
            reply_markup=keyboard,
        )


async def _handle_aita_toggle(query, data: str, kind: str):
    """Toggle a pick's like/dislike state in its session and re-render the keyboard."""
    parts = data.split(":")
    if len(parts) != 3:
        return
    _, sid, idx_s = parts
    try:
        idx = int(idx_s)
    except ValueError:
        return
    s = _aita_sessions.get(sid)
    if s is None:
        await query.edit_message_text(
            text=query.message.text + "\n\n(Session expired — run /aita again)",
            parse_mode="Markdown",
        )
        return
    picks = s["picks"]
    if not (1 <= idx <= len(picks)):
        return
    pid = picks[idx - 1]["id"]
    target = s["liked"] if kind == "like" else s["disliked"]
    other  = s["disliked"] if kind == "like" else s["liked"]
    if pid in target:
        target.discard(pid)  # tap again to clear
    else:
        target.add(pid)
        other.discard(pid)   # like/dislike are mutually exclusive

    new_text = _render_picker_text(picks, s["liked"], s["disliked"])
    new_kb   = _render_picker_keyboard(picks, sid)
    try:
        await query.edit_message_text(
            text=new_text, reply_markup=new_kb, parse_mode="Markdown",
        )
    except Exception:
        # No-op edit (Telegram error if text unchanged) — safe to ignore
        pass


async def _dispatch_vault_aita_block(disliked_posts: list[dict]):
    """Fire-and-forget A2A: tell vault curator to log the new disliked hooks."""
    import subprocess
    from pathlib import Path
    titles = "\n".join(f"- {p['title']} (reddit id: {p['id']})" for p in disliked_posts)
    task = (
        "Log these AITA hooks as DISLIKED in vault — they should never be "
        "recommended again. Append them to a 'Disliked Hooks (block-list)' "
        "section in Projects & Building/Content Creation/AITA Storytime Playbook.md "
        "(create the section if missing). Include reddit id and the date "
        f"{time.strftime('%Y-%m-%d')}.\n\n"
        f"Disliked hooks:\n{titles}"
    )
    try:
        await asyncio.to_thread(
            subprocess.Popen,
            ["bash", str(Path.home() / "agentic_os" / "tools" / "dispatch.sh"),
             "vault_curator", task],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        log.warning("Vault dispatch failed: %s", e)


async def _handle_aita_done(query, data: str):
    """Commit the user's like/dislike choices and start render previews for likes."""
    import aita_pipeline
    sid = data.split(":", 1)[1] if ":" in data else ""
    s = _aita_sessions.pop(sid, None)
    if s is None:
        await query.edit_message_text(
            text=query.message.text + "\n\n(Already submitted)",
            parse_mode="Markdown",
        )
        return

    picks   = s["picks"]
    by_id   = {p["id"]: p for p in picks}
    liked   = [by_id[i] for i in s["liked"]    if i in by_id]
    disliked= [by_id[i] for i in s["disliked"] if i in by_id]

    # Persist dislikes + dispatch vault curator
    if disliked:
        try:
            aita_pipeline.add_to_blocklist(
                [{"id": p["id"], "title": p["title"]} for p in disliked]
            )
        except Exception as e:
            log.warning("Blocklist write failed: %s", e)
        await _dispatch_vault_aita_block(disliked)

    # Edit the picker message to a summary
    summary = [f"📜 *Saved* — {len(liked)} liked, {len(disliked)} blocked forever", ""]
    if liked:
        summary.append("*✅ Liked — going to preview:*")
        for p in liked:
            summary.append(f"  • {p['title'][:90]}")
    if disliked:
        summary.append("*🚫 Blocked from future picks (vault notified):*")
        for p in disliked:
            summary.append(f"  • {p['title'][:90]}")
    if not liked and not disliked:
        summary = ["📜 No selections — closed."]
    try:
        await query.edit_message_text(
            text="\n".join(summary), parse_mode="Markdown",
        )
    except Exception:
        pass

    # Spawn one preview message per liked hook
    for p in liked:
        await _send_aita_preview(p)


async def _handle_aita_skip(query, data: str):
    """User wants 4 fresh picks (excluding shown + blocked). Replace the current session."""
    import aita_pipeline
    sid = data.split(":", 1)[1] if ":" in data else ""
    _aita_sessions.pop(sid, None)

    await query.edit_message_text(
        text=query.message.text + "\n\n🔄 Skipped — fetching 4 more picks…",
        parse_mode="Markdown",
    )

    try:
        picks = await asyncio.to_thread(aita_pipeline.fetch_fresh_picks, 4)
    except Exception as e:
        log.exception("AITA fetch_fresh_picks failed")
        await _app.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"⚠️ Fetch failed: {e}",
        )
        return
    if not picks:
        # Hot feed exhausted for today (everything passing filters has been shown).
        # Offer a manual reset so user isn't stuck.
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Reset today's shown list", callback_data="aita_reset:0"),
        ]])
        await _app.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=(
                "📭 No more *fresh* hooks in r/AITA right now — you've already seen "
                "every qualifying post in the hot feed today.\n\n"
                "Tap below to wipe today's shown-list and re-surface them "
                "(your forever-blocklist of disliked hooks is preserved). "
                "Or wait 30–60 min and run /aita again."
            ),
            reply_markup=kb, parse_mode="Markdown",
        )
        return
    aita_pipeline.save_picks_for_today(picks)
    await send_aita_picks(picks)


async def _handle_aita_reset(query, data: str):
    """Wipe today's shown-list and re-run the picker."""
    import aita_pipeline
    n = await asyncio.to_thread(aita_pipeline.reset_shown_today)
    await query.edit_message_text(
        text=f"🧹 Cleared {n} shown ID(s) — fetching fresh picks…",
        parse_mode="Markdown",
    )
    try:
        picks = await asyncio.to_thread(aita_pipeline.fetch_top_posts)
    except Exception as e:
        await _app.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"⚠️ Fetch failed: {e}",
        )
        return
    if not picks:
        await _app.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text="Still nothing qualifying in the hot feed — try later.",
        )
        return
    picks.sort(key=aita_pipeline.score_hook, reverse=True)
    top5 = picks[:5]
    aita_pipeline.save_picks_for_today(top5)
    await send_aita_picks(top5)


async def _handle_aita_render(query, data: str):
    """Stage 2: user approved — enqueue the render job and return immediately.

    Renders run serially on a background worker so multiple clicks don't pile
    up behind a 4-minute blocking handler (which causes Telegram's callback
    queries to expire with "Query is too old").
    """
    action_id = data.split(":", 1)[1] if ":" in data else ""
    entry = _aita_pending.pop(action_id, None)
    if entry is None:
        await query.edit_message_text(
            text=query.message.text + "\n\n(Already handled or expired)",
            parse_mode="Markdown",
        )
        return

    raw_paths = entry.get("script_paths") or [entry["script_path"]]
    title = entry["title"]
    n_parts = len(raw_paths)

    _ensure_render_worker()
    await _render_queue.put({
        "script_paths": raw_paths,
        "title": title,
    })
    queue_pos = _render_queue.qsize()  # jobs ahead, including this one

    eta = f"~{n_parts * 4} min" if n_parts > 1 else "~3–5 minutes"
    status = f"🎬 Queued render: _{title}_"
    if n_parts > 1:
        status += f"\n📺 {n_parts} parts — sent as each finishes"
    if queue_pos > 1:
        status += f"\n⏳ Position {queue_pos} in render queue"
    status += f"\n\nRender time: {eta}."
    await query.edit_message_text(text=status, parse_mode="Markdown")


async def _handle_aita_cancel(query, data: str):
    """Discard the pending script."""
    action_id = data.split(":", 1)[1] if ":" in data else ""
    _aita_pending.pop(action_id, None)
    await query.edit_message_text(
        text=query.message.text + "\n\n🗑 Cancelled.",
        parse_mode="Markdown",
    )


# Active multi-select picker sessions
# sid → {"picks": [post dicts], "liked": set[post_id], "disliked": set[post_id]}
_aita_sessions: dict[str, dict] = {}


def _new_aita_sid() -> str:
    import uuid
    return uuid.uuid4().hex[:8]


def _render_picker_text(picks: list[dict], liked: set[str], disliked: set[str]) -> str:
    import aita_pipeline
    lines = [
        "📜 *Today's storytime picks*",
        "_Tap 👍 to like, 👎 to block forever — multi-select. Then **Done**._",
        "",
    ]
    for i, p in enumerate(picks, 1):
        pid = p["id"]
        if pid in liked:
            marker = "✅"
        elif pid in disliked:
            marker = "🚫"
        else:
            marker = "▫️"
        title = p["title"][:110]
        ups = p.get("ups", 0)
        sub = p.get("sub", "")
        short = aita_pipeline.SUB_META.get(sub, {}).get("short", sub or "?")
        lines.append(f"{marker} *{i}.* `[{short}]` [{ups}] {title}")
    return "\n".join(lines)


def _render_picker_keyboard(picks: list[dict], sid: str) -> InlineKeyboardMarkup:
    rows = []
    for i in range(1, len(picks) + 1):
        rows.append([
            InlineKeyboardButton(f"👍 #{i}", callback_data=f"aita_like:{sid}:{i}"),
            InlineKeyboardButton(f"👎 #{i}", callback_data=f"aita_dislike:{sid}:{i}"),
        ])
    rows.append([
        InlineKeyboardButton("✅ Done", callback_data=f"aita_done:{sid}"),
        InlineKeyboardButton("🔄 4 more", callback_data=f"aita_skip:{sid}"),
    ])
    return InlineKeyboardMarkup(rows)


async def send_aita_picks(picks: list[dict]):
    """Multi-select AITA picker — Like / Dislike per hook + Done / 4-more."""
    if _app is None:
        log.warning("Bot not ready, cannot send AITA picks")
        return

    sid = _new_aita_sid()
    _aita_sessions[sid] = {"picks": picks, "liked": set(), "disliked": set()}

    text = _render_picker_text(picks, set(), set())
    keyboard = _render_picker_keyboard(picks, sid)
    try:
        await _app.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID, text=text,
            reply_markup=keyboard, parse_mode="Markdown",
        )
    except Exception:
        await _app.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=text.replace("*", ""), reply_markup=keyboard,
        )


async def send_video(path, caption: str = ""):
    """Send a local video file to Taran (post-render delivery)."""
    if _app is None:
        log.warning("Bot not ready, cannot send video")
        return
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        await _app.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"⚠️ Video missing at {p}",
        )
        return
    with open(p, "rb") as f:
        await _app.bot.send_video(
            chat_id=TELEGRAM_CHAT_ID,
            video=f,
            caption=caption,
            supports_streaming=True,
        )


async def send_message(text: str):
    """Send a message to Taran. Splits at paragraph/line breaks near 4096-char limit."""
    if _app is None:
        log.warning("Bot not ready, cannot send message")
        return
    MAX = 4096
    # Smart-split: prefer paragraph breaks, then line breaks, then hard cut
    chunks: list[str] = []
    remaining = text
    while len(remaining) > MAX:
        split = remaining.rfind("\n\n", 0, MAX)
        if split == -1:
            split = remaining.rfind("\n", 0, MAX)
        if split == -1:
            split = MAX
        chunks.append(remaining[:split])
        remaining = remaining[split:].lstrip("\n")
    if remaining:
        chunks.append(remaining)

    for chunk in chunks:
        try:
            await _app.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=chunk,
                parse_mode="Markdown",
            )
        except Exception:
            # Markdown parse error — retry as plain text so the content still arrives
            try:
                await _app.bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=chunk,
                )
            except Exception as e:
                log.error("Failed to send message chunk: %s", e)


async def send_photo(image_bytes: bytes, caption: str = ""):
    """Send a photo to Taran. Called after application form fill."""
    if _app is None:
        log.warning("Bot not ready, cannot send photo")
        return
    await _app.bot.send_photo(
        chat_id=TELEGRAM_CHAT_ID,
        photo=image_bytes,
        caption=caption,
        parse_mode="Markdown",
    )


async def send_approval_request(action_id: str, text: str):
    """Send an approval request with Approve/Deny buttons."""
    if _app is None:
        log.warning("Bot not ready, cannot send approval request")
        return
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Approve ✅", callback_data=f"approve:{action_id}"),
            InlineKeyboardButton("Deny ❌", callback_data=f"deny:{action_id}"),
        ]
    ])
    await _app.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=f"*Approval required* (ID: `{action_id}`)\n\nPlease check the Dashboard to review the full details and approve/deny.",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


async def send_routing_request(action_id: str, task: str):
    """Send a routing request with multiple buttons."""
    if _app is None:
        log.warning("Bot not ready, cannot send routing request")
        return
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💼 Career", callback_data=f"route_career:{action_id}"),
            InlineKeyboardButton("💰 Finance", callback_data=f"route_finance:{action_id}"),
        ],
        [
            InlineKeyboardButton("📋 Briefing", callback_data=f"route_briefing:{action_id}"),
            InlineKeyboardButton("📚 Study", callback_data=f"route_study:{action_id}"),
        ],
        [
            InlineKeyboardButton("🎨 Content", callback_data=f"route_content:{action_id}"),
            InlineKeyboardButton("🗄️ Vault Curator", callback_data=f"route_vault_curator:{action_id}"),
        ],
        [
            InlineKeyboardButton("⌨️ Coding", callback_data=f"route_coding:{action_id}"),
        ],
        [
            InlineKeyboardButton("🤖 General", callback_data=f"route_general:{action_id}"),
        ],
    ])
    await _app.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=f"*Where should I route this task?*\n\n`{task}`",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


async def cmd_cred_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _guard(update):
        return
    if len(context.args) < 3:
        await update.message.reply_text("Usage: /cred_add <service> <username> <password>")
        return
    service, username, *pw_parts = context.args
    password = " ".join(pw_parts)
    from tools import credentials
    try:
        credentials.store(service, username, password)
    except Exception as e:
        await update.message.reply_text(f"Store failed: {e}")
        return
    try:
        await update.message.delete()
    except Exception:
        pass
    await update.message.reply_text(f"Stored credential for {service} (your message was deleted).")


async def cmd_cred_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _guard(update):
        return
    from tools import credentials
    entries = credentials.list_services()
    if not entries:
        await update.message.reply_text("No stored credentials.")
        return
    lines = [f"• {e['service']}  →  {e['account']}" for e in entries]
    await update.message.reply_text("Stored credentials:\n" + "\n".join(lines))


async def cmd_cred_get(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _guard(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /cred_get <service>")
        return
    from tools import credentials
    entry = credentials.get(context.args[0])
    if not entry:
        await update.message.reply_text("Not found.")
        return
    await update.message.reply_text(
        f"{entry['service']}\nUser: {entry['account']}\nPass: {entry['password']}"
    )


async def cmd_cred_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _guard(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /cred_del <service>")
        return
    from tools import credentials
    ok = credentials.delete(context.args[0])
    await update.message.reply_text("Deleted." if ok else "Not found.")


def build_app() -> Application:
    global _app
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("task", cmd_task))
    app.add_handler(CommandHandler("fill", cmd_fill))
    app.add_handler(CommandHandler("size", cmd_size))
    app.add_handler(CommandHandler("aita", cmd_aita))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("cred_add", cmd_cred_add))
    app.add_handler(CommandHandler("cred_list", cmd_cred_list))
    app.add_handler(CommandHandler("cred_get", cmd_cred_get))
    app.add_handler(CommandHandler("cred_del", cmd_cred_del))
    app.add_handler(CallbackQueryHandler(handle_approval_callback))
    _app = app

    # Wire the approval gate sender
    approval_gate.register_sender(send_approval_request)

    return app
