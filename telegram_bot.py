"""
Telegram bot — two responsibilities:
1. /task <description>  → submits a task to the orchestrator queue
2. Approve/Deny buttons → resolves pending approval gates
"""

import asyncio
import logging
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
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

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
        "/status — show queue and pending approvals\n"
        "/help — this message"
    )


async def handle_approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if update.effective_chat.id != TELEGRAM_CHAT_ID:
        return

    data = query.data  # e.g. "approve:abc12345", "deny:abc12345", "route_career:abc12345"
    
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


async def send_message(text: str):
    """Send a plain message to Taran. Called by orchestrator tool handlers."""
    if _app is None:
        log.warning("Bot not ready, cannot send message")
        return
    await _app.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=text,
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
        text=f"*Approval required* (ID: `{action_id}`)\n\n{text}",
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
            InlineKeyboardButton("🏠 Personal", callback_data=f"route_personal:{action_id}"),
        ],
        [
            InlineKeyboardButton("🔍 Review", callback_data=f"route_review:{action_id}"),
            InlineKeyboardButton("🤖 General", callback_data=f"route_general:{action_id}"),
        ]
    ])
    await _app.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=f"*Where should I route this task?*\n\n`{task}`",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


def build_app() -> Application:
    global _app
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("task", cmd_task))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(handle_approval_callback))
    _app = app

    # Wire the approval gate sender
    approval_gate.register_sender(send_approval_request)

    return app
