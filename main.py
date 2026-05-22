"""
PAIS — entry point.
True concurrent multi-agent: four independent workers run in parallel.
Each agent (career / personal / review / general) has its own queue.
Routing is non-blocking — new tasks can be submitted while others run.
"""

import asyncio
import datetime
import json
import logging
import sys
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import JSONResponse, FileResponse

from config import DASHBOARD_PORT
from scheduler import (
    ScheduledTask, create_schedule, delete_schedule, toggle_schedule,
    load_schedules, schedule_to_dict, run_scheduler,
)

_LOG_FILE = Path(__file__).parent / "pais.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        # Rotate at 10 MB, keep 3 backups — caps pais.log at ~40 MB total.
        RotatingFileHandler(
            _LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
        ),
    ],
)
log = logging.getLogger(__name__)

# ── Global State ─────────────────────────────────────────────────────────────

_ws_clients: list[WebSocket] = []
_session_tokens: int = 0
_weekly_tokens: int = 0
_session_tasks_done: int = 0
# Per-agent breakdown for the current week: agent → {input, output, calls, model}.
# Reset together with _weekly_tokens at week rollover in _load_usage().
_weekly_by_agent: dict[str, dict] = {}
_pending_verifications: dict[str, dict] = {}  # tid → {agent, text, result}
_pending_requeues: dict[str, dict] = {}        # correction_tid → {agent, text} to requeue after correction

# Per-agent queues — each has its own independent worker
_career_q:        asyncio.Queue = asyncio.Queue()
_general_q:       asyncio.Queue = asyncio.Queue()  # merged personal+general
_finance_q:       asyncio.Queue = asyncio.Queue()
_briefing_q:      asyncio.Queue = asyncio.Queue()
_study_q:         asyncio.Queue = asyncio.Queue()
_content_q:       asyncio.Queue = asyncio.Queue()
_vault_curator_q: asyncio.Queue = asyncio.Queue()
_coding_q:        asyncio.Queue = asyncio.Queue()

# Retry queue — failed tasks re-enter with backoff metadata
_retry_q:    asyncio.Queue = asyncio.Queue()

# Backoff delays in seconds for retry attempts 1, 2, 3
_RETRY_BACKOFF = [30, 90, 270]
_MAX_RETRIES   = 3

AGENTIC_DIR    = Path(__file__).parent
USAGE_FILE     = AGENTIC_DIR / "usage.json"
LESSONS_FILE   = AGENTIC_DIR / "lessons.md"
ACTIVITY_FILE  = AGENTIC_DIR / "activity.json"
BASE_VAULT     = Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain"
TASKS_DIR      = BASE_VAULT / "PAIS Hub" / "Tasks"
UPLOADS_DIR    = AGENTIC_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

# Content renders — finished TikTok-bound videos, grouped by pipeline/channel.
# The Content page (/content) lists these so renders can be reviewed and posted
# from PAIS instead of being pushed as heavy uploads over Telegram.
RENDER_CHANNELS = {
    "reddit": {
        "name":   "Reddit",
        "desc":   "AITA storytime renders",
        "icon":   "👁",
        "dir":    Path.home() / "Desktop" / "AITA Renders",
        "tiktok": "https://www.tiktok.com/@redditverdict.tv",
    },
    "motivational": {
        "name":   "Motivational",
        "desc":   "Stoic / motivational renders",
        "icon":   "🏛",
        "dir":    Path.home() / "Desktop" / "Stoic Renders",
        "tiktok": "",
    },
    "horror": {
        "name":   "Horror",
        "desc":   "Creepypasta & dark storytime renders",
        "icon":   "🕯",
        "dir":    Path.home() / "Desktop" / "Horror Renders",
        "tiktok": "",
    },
}

_activity_log: list[dict] = []
_MAX_ACTIVITY = 100

# ── FastAPI ───────────────────────────────────────────────────────────────────

api  = FastAPI(title="PAIS")
_DASH = Path(__file__).parent / "dashboard"


@api.get("/")
async def srv_dash():     return FileResponse(str(_DASH / "index.html"), media_type="text/html")
@api.get("/career")
async def srv_career():   return FileResponse(str(_DASH / "career.html"), media_type="text/html")
@api.get("/mobile")
async def srv_mobile():   return FileResponse(str(_DASH / "mobile.html"), media_type="text/html")
@api.get("/trades")
async def srv_trades():   return FileResponse(str(_DASH / "trades.html"), media_type="text/html")
@api.get("/content")
async def srv_content():  return FileResponse(str(_DASH / "content.html"), media_type="text/html")
@api.get("/manifest.json")
async def srv_manifest(): return FileResponse(str(_DASH / "manifest.json"), media_type="application/manifest+json")
@api.get("/sw.js")
async def srv_sw():       return FileResponse(str(_DASH / "sw.js"), media_type="application/javascript")

# PAIS branding assets
@api.get("/favicon.ico")
async def srv_favicon_ico():   return FileResponse(str(_DASH / "favicon-32.png"), media_type="image/png")
@api.get("/favicon-32.png")
async def srv_favicon_32():    return FileResponse(str(_DASH / "favicon-32.png"), media_type="image/png")
@api.get("/apple-touch-icon.png")
async def srv_apple_icon():    return FileResponse(str(_DASH / "apple-touch-icon.png"), media_type="image/png")
@api.get("/icon-192.png")
async def srv_icon_192():      return FileResponse(str(_DASH / "icon-192.png"), media_type="image/png")
@api.get("/icon-512.png")
async def srv_icon_512():      return FileResponse(str(_DASH / "icon-512.png"), media_type="image/png")


# ── Broadcast ─────────────────────────────────────────────────────────────────

async def broadcast(event: dict):
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in _ws_clients:
            _ws_clients.remove(ws)


# ── WebSocket ─────────────────────────────────────────────────────────────────

@api.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.append(ws)
    try:
        while True:
            data = await ws.receive_text()
            msg  = json.loads(data)
            t    = msg.get("type")

            if t == "chat":
                tid   = _make_id()
                text  = msg.get("text", "")
                agent = msg.get("agent", "auto")
                _agent_queue_map = {
                    "career":       (_career_q,       "career"),
                    "finance":      (_finance_q,      "finance"),
                    "briefing":     (_briefing_q,     "briefing"),
                    "study":        (_study_q,        "study"),
                    "content":      (_content_q,      "content"),
                    "vault_curator":(_vault_curator_q,"vault_curator"),
                }
                if agent in _agent_queue_map:
                    q, agent_name = _agent_queue_map[agent]
                    await q.put({"id": tid, "text": text})
                    await broadcast({"type": "task_dispatched", "id": tid, "agent": agent_name, "text": text})
                else:
                    # auto or general → conversational chat path
                    await broadcast({"type": "task_dispatched", "id": tid, "agent": "general", "text": text})
                    asyncio.create_task(_run_chat_task(tid, text))

            elif t == "task":
                await _handle_new_task(msg["text"])

            elif t == "career_start":
                tid = _make_id()
                keywords = msg.get("keywords") or msg.get("task", "software engineering")
                await _career_q.put({"id": tid, "text": keywords})
                await broadcast({"type": "task_dispatched", "id": tid, "agent": "career",
                                 "text": keywords})

            elif t in ("personal_start", "general_start"):
                # personal and general are merged into one agent
                tid = _make_id()
                await _general_q.put({"id": tid, "text": msg.get("task", msg.get("text", ""))})
                await broadcast({"type": "task_dispatched", "id": tid, "agent": "general",
                                 "text": msg.get("task", msg.get("text", ""))})

            elif t == "finance_start":
                tid = _make_id()
                await _finance_q.put({"id": tid, "text": msg.get("task", msg.get("text", "weekly digest"))})
                await broadcast({"type": "task_dispatched", "id": tid, "agent": "finance",
                                 "text": msg.get("task", msg.get("text", "weekly digest"))})

            elif t == "briefing_start":
                tid = _make_id()
                await _briefing_q.put({"id": tid, "text": msg.get("task", msg.get("text", "weekly briefing"))})
                await broadcast({"type": "task_dispatched", "id": tid, "agent": "briefing",
                                 "text": msg.get("task", msg.get("text", "weekly briefing"))})

            elif t == "study_start":
                tid = _make_id()
                await _study_q.put({"id": tid, "text": msg.get("task", msg.get("text", ""))})
                await broadcast({"type": "task_dispatched", "id": tid, "agent": "study",
                                 "text": msg.get("task", msg.get("text", ""))})

            elif t == "content_start":
                tid = _make_id()
                await _content_q.put({"id": tid, "text": msg.get("task", msg.get("text", ""))})
                await broadcast({"type": "task_dispatched", "id": tid, "agent": "content",
                                 "text": msg.get("task", msg.get("text", ""))})

            elif t == "coding_start":
                tid = _make_id()
                await _coding_q.put({"id": tid, "text": msg.get("task", msg.get("text", ""))})
                await broadcast({"type": "task_dispatched", "id": tid, "agent": "coding",
                                 "text": msg.get("task", msg.get("text", ""))})

            elif t == "vault_curator_start":
                tid = _make_id()
                await _vault_curator_q.put({"id": tid, "text": msg.get("task", msg.get("text", "maintenance"))})
                await broadcast({"type": "task_dispatched", "id": tid, "agent": "vault_curator",
                                 "text": msg.get("task", msg.get("text", "maintenance"))})

            elif t == "route":
                tid    = msg.get("id")
                choice = msg.get("choice")
                import tools.approval as ag
                ag.resolve(tid, choice)
                await broadcast({"type": "routed", "id": tid, "choice": choice})

            elif t == "stop":
                from tools.llm import stop_active_proc
                target_id = msg.get("id")  # may be None → stop all
                if stop_active_proc(task_id=target_id):
                    await broadcast({"type": "task_error", "id": target_id,
                                     "text": "Task stopped by user."})

            elif t == "approve":
                import tools.approval as ag
                ag.resolve(msg["id"], "approved")
                await broadcast({"type": "approved", "id": msg["id"]})

            elif t == "deny":
                import tools.approval as ag
                ag.resolve(msg["id"], "denied")
                await broadcast({"type": "denied", "id": msg["id"]})

            elif t == "task_interact":
                tid    = msg.get("id")
                action = msg.get("action", "complete")
                if action == "followup":
                    asyncio.create_task(_followup_verification(tid, msg.get("text", "")))
                else:
                    asyncio.create_task(_complete_verification(tid))

            elif t == "task_correction":
                asyncio.create_task(
                    _handle_task_correction(msg.get("id"), msg.get("feedback", ""))
                )

    except WebSocketDisconnect:
        if ws in _ws_clients:
            _ws_clients.remove(ws)
    except Exception as e:
        log.warning("WS error: %s", e)


# ── Routing ───────────────────────────────────────────────────────────────────

async def _run_chat_task(tid: str, text: str):
    """Run a direct chat message — bypasses routing but still verifies so the
    self-correction loop is reachable from every chat message."""
    from tools.llm import set_current_task_id
    set_current_task_id(tid)
    await broadcast({"type": "task_started", "id": tid, "text": text, "agent": "general"})
    try:
        from orchestrator import run_chat
        # Token accounting is now handled by the usage_callback registered on startup
        result, _, _ = await run_chat(text, broadcast=broadcast, task_id=tid)
        await _log_activity(tid, "general", text, result)
        _pending_verifications[tid] = {
            "agent": "general", "text": text, "result": result,
            "ts": datetime.datetime.now().timestamp(),
        }
        await broadcast({"type": "task_verification_required",
                         "id": tid, "text": result, "prompt": text})
    except Exception as e:
        log.exception("chat task error")
        await broadcast({"type": "task_error", "id": tid, "text": str(e)})


async def _handle_new_task(text: str):
    """Non-blocking: show routing card + dispatch background wait."""
    tid  = _make_id()
    loop = asyncio.get_running_loop()
    fut  = loop.create_future()

    import tools.approval as ag
    ag.register_route_future(tid, fut)

    await broadcast({"type": "approval_request", "id": tid, "text": text, "action": "route"})
    asyncio.create_task(_tg_route(tid, text))
    asyncio.create_task(_route_and_dispatch(tid, text, fut))


async def _tg_route(tid: str, text: str):
    try:
        from telegram_bot import send_routing_request
        await send_routing_request(tid, text)
    except Exception as e:
        log.warning("TG routing: %s", e)


async def _route_and_dispatch(tid: str, text: str, fut: asyncio.Future):
    try:
        choice = await asyncio.wait_for(fut, timeout=300)
    except asyncio.TimeoutError:
        await broadcast({"type": "task_error", "id": tid, "text": "Routing timed out (5 min)."})
        return

    item = {"id": tid, "text": text}
    q_map = {
        "career": _career_q, "finance": _finance_q, "briefing": _briefing_q,
        "study": _study_q, "content": _content_q,
        "vault_curator": _vault_curator_q, "coding": _coding_q,
    }
    await q_map.get(choice, _general_q).put(item)

    await broadcast({"type": "task_dispatched", "id": tid, "agent": choice, "text": text})


# ── Agent task runner ─────────────────────────────────────────────────────────

async def _run_agent_task(item: dict, agent: str):
    tid     = item["id"]
    text    = item["text"]
    attempt = item.get("attempt", 1)

    log.info("[%s] ▶ attempt=%d %s", agent, attempt, text[:70])
    await broadcast({"type": "task_started", "id": tid, "text": text, "agent": agent})

    # Per-task sandbox directory (isolated working dir)
    sandbox = AGENTIC_DIR / "tmp" / tid
    sandbox.mkdir(parents=True, exist_ok=True)

    # Propagate task_id to tools.llm via contextvar so /stop can target this task
    from tools.llm import set_current_task_id
    set_current_task_id(tid)

    from telegram_bot import send_message as tg

    try:
        if agent == "career":
            from career_workflow import run_career_search
            result = await run_career_search(text, broadcast, tg)
            result = result or "Career search workflow complete."
        elif agent == "finance":
            from finance_workflow import run_finance_task
            result = await run_finance_task(text, broadcast, tg, sandbox_dir=sandbox)
            result = result or "Finance task complete."
        elif agent == "briefing":
            from briefing_workflow import run_briefing_task
            result = await run_briefing_task(text, broadcast, tg, sandbox_dir=sandbox)
            result = result or "Briefing complete."
        elif agent == "study":
            from study_workflow import run_study_task
            result = await run_study_task(text, broadcast, tg, sandbox_dir=sandbox)
            result = result or "Study task complete."
        elif agent == "content":
            from content_workflow import run_content_task
            result = await run_content_task(text, broadcast, tg, sandbox_dir=sandbox)
            result = result or "Content pipeline complete."
        elif agent == "vault_curator":
            from vault_curator_workflow import run_vault_curator_task
            result = await run_vault_curator_task(text, broadcast, tg, sandbox_dir=sandbox)
            result = result or "Vault curation complete."
        elif agent == "coding":
            from coding_workflow import run_coding_task
            result = await run_coding_task(text, broadcast, tg, sandbox_dir=sandbox)
            result = result or "Coding task complete."
        else:
            # general agent — handles personal + general tasks
            # Token accounting is handled by the usage_callback for ALL agents
            from orchestrator import run_task
            result, _, _ = await run_task(text, send_telegram=tg, broadcast=broadcast, task_id=tid)

        # Log to activity feed
        await _log_activity(tid, agent, text, result)

        # Offer vault_curator as opt-in (was auto-dispatched; expensive on tokens).
        # Frontend renders Yes/No card; on Yes it sends {type:"vault_curator_start", task:...}
        if agent != "vault_curator":
            vc_task = (
                f"An agent just finished a task. Review its output and decide if anything "
                f"should be saved or cross-linked in the vault. Then run a quick vault "
                f"inspection (check for broken links, orphaned notes, missing index entries).\n\n"
                f"Agent: {agent}\nTask: {text}\nResult summary: {result[:500]}"
            )
            await broadcast({
                "type": "vault_curator_offer",
                "id": tid,
                "agent": agent,
                "result_summary": result[:300],
                "suggested_task": vc_task,
            })

        # Store verification without blocking the worker
        _pending_verifications[tid] = {
            "agent": agent, "text": text, "result": result,
            "ts": datetime.datetime.now().timestamp(),
        }
        await broadcast({"type": "task_verification_required",
                         "id": tid, "text": result, "prompt": text})
        await _safe_tg(tg, "Task ready — tap Complete or send follow-up.")

    except Exception as e:
        log.exception("[%s] task error (attempt %d)", agent, attempt)
        err_str = str(e)
        err_low = err_str.lower()
        # Don't retry hard user/content errors. Retry transient/system errors.
        # When it's neither, retry (transient is the common case).
        non_retriable = any(kw in err_low for kw in (
            "denied by user", "permission denied", "authentication", "unauthorized",
            "invalid argument", "syntax error",
        ))
        is_retriable = not non_retriable
        if is_retriable and attempt <= _MAX_RETRIES:
            delay = _RETRY_BACKOFF[min(attempt - 1, len(_RETRY_BACKOFF) - 1)]
            log.info("[%s] Scheduling retry %d/%d in %ds", agent, attempt, _MAX_RETRIES, delay)
            retry_item = {**item, "attempt": attempt + 1, "retry_at": datetime.datetime.now().timestamp() + delay}
            await _retry_q.put({"item": retry_item, "agent": agent})
            await broadcast({
                "type": "task_error", "id": tid,
                "text": f"Error (retrying in {delay}s, attempt {attempt}/{_MAX_RETRIES}): {err_str}",
                "agent": agent,
            })
        else:
            await broadcast({"type": "task_error", "id": tid, "text": err_str, "agent": agent})


# ── Verification handlers (called when user clicks Complete / sends follow-up) ─

async def _complete_verification(tid: str):
    global _session_tasks_done
    v = _pending_verifications.pop(tid, None)
    if not v:
        return
    from telegram_bot import send_message as tg
    from tools.logger import log_completed_task
    log_completed_task(
        v["text"][:50],
        f"Agent: {v['agent']}\nTask: {v['text']}\nResult: {v['result'][:400]}",
        actions=["User verified and completed."],
        task_type=v["agent"],
    )
    _session_tasks_done += 1
    _save_usage(force=True)
    await broadcast({"type": "usage_update", "tasks_done": _session_tasks_done})
    await broadcast({"type": "task_done", "id": tid, "text": v["result"], "agent": v["agent"]})
    await _safe_tg(tg, "✅ Task complete.")

    # If this was a correction task, hot-reload modules and re-queue original task
    requeue = _pending_requeues.pop(tid, None)
    if requeue:
        _hot_reload_modules()
        new_id   = _make_id()
        new_item = {"id": new_id, "text": requeue["text"]}
        agent    = requeue["agent"]
        q_map = {
            "career": _career_q, "finance": _finance_q, "briefing": _briefing_q,
            "study": _study_q, "content": _content_q,
            "vault_curator": _vault_curator_q, "coding": _coding_q,
        }
        await q_map.get(agent, _general_q).put(new_item)
        await broadcast({"type": "task_dispatched", "id": new_id, "agent": agent,
                         "text": f"🔄 Retry: {requeue['text']}"})
        await _safe_tg(tg, f"🔄 Re-running original task with fixed code…")


async def _followup_verification(tid: str, followup_text: str):
    v = _pending_verifications.pop(tid, None)
    if not v:
        return
    from telegram_bot import send_message as tg
    new_item = {"id": _make_id(), "text": followup_text}
    q_map = {
        "career": _career_q, "finance": _finance_q, "briefing": _briefing_q,
        "study": _study_q, "content": _content_q,
        "vault_curator": _vault_curator_q, "coding": _coding_q,
    }
    await q_map.get(v["agent"], _general_q).put(new_item)
    await broadcast({"type": "task_dispatched", "id": new_item["id"],
                     "agent": v["agent"], "text": followup_text})
    await _safe_tg(tg, f"Follow-up queued: {followup_text[:80]}")


async def _handle_task_correction(tid: str, feedback: str):
    """User said the task wasn't done correctly — spawn a self-correction task."""
    v = _pending_verifications.pop(tid, None)
    if not v:
        return
    from telegram_bot import send_message as tg

    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    lessons_path = str(LESSONS_FILE)
    code_dir = str(AGENTIC_DIR)

    correction_prompt = f"""You are PAIS performing a self-correction after a failed task.

ORIGINAL TASK: {v['text']}
YOUR RESULT (what you returned):
{v['result'][:800]}

USER FEEDBACK (what went wrong):
{feedback}

Your job — do all of these:

1. Read the code in {code_dir} that handled this task type. Pick the file for the failing agent:
   - career_workflow.py (career searches, job applications)
   - finance_workflow.py (spending, signals, statements)
   - briefing_workflow.py (daily/weekly review)
   - study_workflow.py (study guides, flashcards)
   - content_workflow.py (faceless video pipeline)
   - vault_curator_workflow.py (vault maintenance, cross-links)
   - coding_workflow.py (code review, bug fixes, features)
   - orchestrator.py (general agent — default chat tasks)
   - tools/ directory (for tool-level bugs that affect any agent)

2. Identify the exact root cause of the failure based on the feedback.

3. Fix the code using the Edit tool. Make minimal, targeted changes — only fix the root cause.

4. Append this entry to {lessons_path}:

## [{date_str}] {v['text'][:60]}
**Feedback:** {feedback}
**Root cause:** [what went wrong in the code]
**Fix applied:** [which file + what changed]
**Lesson:** [one-sentence rule for next time]

---

5. Return a 2-3 sentence summary of what was wrong and what you fixed.

Important: Do NOT just re-describe the task. Actually read the code, find the bug, and fix it.
"""

    correction_id = _make_id()
    # After correction verifies, auto-requeue the original task. The `ts` lets
    # _evict_stale_verifications drop this entry if the correction never verifies.
    _pending_requeues[correction_id] = {
        "agent": v["agent"], "text": v["text"],
        "ts": datetime.datetime.now().timestamp(),
    }

    await _general_q.put({"id": correction_id, "text": correction_prompt})
    await broadcast({
        "type": "task_dispatched", "id": correction_id, "agent": "general",
        "text": f"🔧 Self-correction: {v['text'][:60]}…",
    })
    await broadcast({
        "type": "task_correction_started", "original_id": tid, "correction_id": correction_id,
        "feedback": feedback,
    })
    await _safe_tg(tg, f"🔧 Analyzing failure and fixing code…\nFeedback: {feedback[:120]}")


def _hot_reload_modules():
    """Hot-reload workflow modules so fixes take effect without full restart."""
    import importlib, sys
    modules_to_reload = [
        # Workflows
        "orchestrator",
        "career_workflow", "finance_workflow",
        "briefing_workflow", "study_workflow",
        "content_workflow", "vault_curator_workflow", "coding_workflow",
        # Tools
        "tools.llm", "tools.vault", "tools.web", "tools.rag",
        "tools.trade_tracker", "tools.jobsearch", "tools.tracker",
        "tools.github_tools", "tools.devtools",
        # Signal monitor
        "dr_profit_monitor",
    ]
    for name in modules_to_reload:
        if name in sys.modules:
            try:
                importlib.reload(sys.modules[name])
                log.info("Hot-reloaded: %s", name)
            except Exception as e:
                log.warning("Hot-reload failed for %s: %s", name, e)


# ── Workers ───────────────────────────────────────────────────────────────────

async def _worker(q: asyncio.Queue, agent: str):
    while True:
        item = await q.get()
        try:
            await _run_agent_task(item, agent)
        except Exception as e:
            log.exception("[%s] worker error: %s", agent, e)
        finally:
            q.task_done()


async def _start_dr_profit_monitor():
    """Start the Dr. Profit Pyrogram monitor. Silently skips if not configured."""
    try:
        from dr_profit_monitor import start_monitor
        await start_monitor()
    except Exception as e:
        log.info("[dr_profit] Monitor not started: %s", e)


@api.post("/api/aita_start")
async def aita_start():
    """Manually trigger the AITA workflow — fetch top 5 hooks and send Telegram picker.

    Replaces the previous 11am cron. Triggered by the 🎬 Run AITA Workflow
    button on the Content agent page, or any other on-demand caller.
    """
    try:
        import aita_pipeline
        from telegram_bot import send_aita_picks
        picks = await asyncio.to_thread(aita_pipeline.fetch_top_posts)
    except Exception as e:
        log.exception("[aita] fetch failed")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    if not picks:
        return JSONResponse({"ok": False, "error": "No qualifying AITA posts in the hot feed right now."})

    # Filter against the per-day shown list so dashboard taps don't re-surface
    # hooks the user already saw; on full exhaust, offer a Reset button via TG.
    fresh = await asyncio.to_thread(aita_pipeline.fetch_fresh_picks, 5)
    if not fresh:
        from telegram_bot import _handle_aita_skip  # not ideal, but reuse the empty-feed UX
        # Send the reset-prompt message directly via the bot:
        from telegram_bot import _app, TELEGRAM_CHAT_ID
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Reset today's shown list", callback_data="aita_reset:0"),
        ]])
        await _app.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=(
                "📭 No *fresh* hooks left in r/AITA today — you've already seen every "
                "qualifying post.\n\nTap below to wipe today's shown-list and re-surface them."
            ),
            reply_markup=kb, parse_mode="Markdown",
        )
        return JSONResponse({"ok": True, "count": 0, "note": "exhausted — reset prompt sent"})

    aita_pipeline.save_picks_for_today(fresh)
    await send_aita_picks(fresh)
    log.info("[aita] /api/aita_start sent %d picks to Telegram", len(fresh))
    return JSONResponse({"ok": True, "count": len(fresh)})


async def _retry_worker():
    """Dequeues failed tasks after their backoff delay and re-dispatches them."""
    while True:
        entry = await _retry_q.get()
        try:
            item  = entry["item"]
            agent = entry["agent"]
            wait  = max(0.0, item.get("retry_at", 0) - datetime.datetime.now().timestamp())
            if wait > 0:
                await asyncio.sleep(wait)
            log.info("[retry] Re-dispatching attempt %d for: %s", item.get("attempt", 2), item["text"][:60])
            q_map = {
                "career": _career_q, "finance": _finance_q, "briefing": _briefing_q,
                "study": _study_q, "content": _content_q,
                "vault_curator": _vault_curator_q, "coding": _coding_q,
            }
            await q_map.get(agent, _general_q).put(item)
            await broadcast({"type": "task_dispatched", "id": item["id"], "agent": agent,
                             "text": f"🔄 Retry #{item.get('attempt',2)}: {item['text']}"})
        except Exception as e:
            log.exception("[retry] worker error: %s", e)
        finally:
            _retry_q.task_done()


async def _schedule_dispatch(task: ScheduledTask):
    """Called by the scheduler when a task is due."""
    tid  = _make_id()
    item = {"id": tid, "text": task.text}
    q_map = {
        "career": _career_q, "finance": _finance_q, "briefing": _briefing_q,
        "study": _study_q, "content": _content_q,
        "vault_curator": _vault_curator_q, "coding": _coding_q,
    }
    await q_map.get(task.agent, _general_q).put(item)
    await broadcast({
        "type": "task_dispatched", "id": tid, "agent": task.agent,
        "text": f"⏰ Scheduled: {task.name} — {task.text}",
    })
    log.info("[scheduler] Dispatched '%s' to %s worker", task.name, task.agent)


# ── API ───────────────────────────────────────────────────────────────────────

@api.get("/api/status")
async def api_status():
    from orchestrator import is_running as g_run
    from career_workflow import is_running as c_run
    from finance_workflow import is_running as fin_run
    from briefing_workflow import is_running as brf_run
    from study_workflow import is_running as stu_run
    from content_workflow import is_running as cnt_run
    from vault_curator_workflow import is_running as vc_run
    from coding_workflow import is_running as cod_run
    from tools.llm import get_preferred_provider
    from tools.usage_quota import fetch_quota
    import tools.approval as ag

    all_agents = ("career", "general", "finance", "briefing", "study", "content", "vault_curator", "coding")
    verifying = {a: any(v["agent"] == a for v in _pending_verifications.values()) for a in all_agents}

    all_queues = [_career_q, _general_q, _finance_q, _briefing_q, _study_q, _content_q, _vault_curator_q, _coding_q]

    return {
        "running": any([c_run(), g_run(), fin_run(), brf_run(), stu_run(), cnt_run(), vc_run(), cod_run()]),
        "running_by_agent": {
            "career": c_run(), "general": g_run(),
            "finance": fin_run(), "briefing": brf_run(),
            "study": stu_run(),
            "content": cnt_run(), "vault_curator": vc_run(), "coding": cod_run(),
        },
        "verifying_by_agent": verifying,
        "pending_verifications": list(_pending_verifications.keys()),
        "queued": sum(q.qsize() for q in all_queues),
        "queued_by_agent": {
            "career": _career_q.qsize(), "general": _general_q.qsize(),
            "finance": _finance_q.qsize(), "briefing": _briefing_q.qsize(),
            "study": _study_q.qsize(),
            "content": _content_q.qsize(), "vault_curator": _vault_curator_q.qsize(), "coding": _coding_q.qsize(),
        },
        "pending_approvals": ag.pending_ids(),
        "session_tokens": _session_tokens,
        "weekly_tokens":  _weekly_tokens,
        "weekly_by_agent": _weekly_by_agent,
        "tasks_done":     _session_tasks_done,
        "provider":       get_preferred_provider(),
        "quota":          await asyncio.get_running_loop().run_in_executor(None, fetch_quota),
    }


@api.get("/api/usage")
async def api_usage():
    """Subscription quota (5h + weekly utilization) plus this week's token spend."""
    from tools.usage_quota import fetch_quota
    quota = await asyncio.get_running_loop().run_in_executor(None, fetch_quota)
    return {
        "quota":           quota,
        "weekly_tokens":   _weekly_tokens,
        "weekly_by_agent": _weekly_by_agent,
        "tasks_done":      _session_tasks_done,
    }


@api.get("/api/history")
async def api_history(filter: str = "all"):
    if not TASKS_DIR.exists():
        return JSONResponse({"history": []})
    tasks = []
    # Sort by file mtime — newest first, regardless of filename format
    files = sorted(TASKS_DIR.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)[:80]
    for f in files:
        try:
            lines     = f.read_text(encoding="utf-8").split("\n")
            name      = lines[0].lstrip("# ").strip() if lines else f.stem
            status    = "COMPLETED"
            task_type = "general"
            requested = ""
            for line in lines:
                if line.startswith("## Status:"):
                    status = line.replace("## Status:", "").strip()
                if line.startswith("## Type:"):
                    task_type = line.replace("## Type:", "").strip()
                if line.startswith("## Requested:"):
                    requested = line.replace("## Requested:", "").strip()

            # Fall back to mtime for files written by old logger
            if not requested:
                mtime = datetime.datetime.fromtimestamp(f.stat().st_mtime)
                requested = mtime.strftime("%Y-%m-%d %H:%M")

            # Heuristic fallback when type not in file
            if task_type == "general":
                if "Code Review" in name:  task_type = "review"
                elif "Career" in name:     task_type = "career"
                else:                      task_type = "personal"

            if filter != "all" and task_type != filter:
                continue

            tasks.append({
                "id":        f.stem,
                "text":      name,
                "time":      requested,
                "status":    status,
                "task_type": task_type,
            })
        except Exception:
            continue
    return JSONResponse({"history": tasks})


@api.get("/api/task_details")
async def api_task_details(id: str):
    if TASKS_DIR.exists():
        for f in TASKS_DIR.glob("*.md"):
            if f.stem == id or id in f.stem:
                return JSONResponse({"ok": True, "content": f.read_text(encoding="utf-8")})
    return JSONResponse({"ok": False, "reason": "Task not found in vault."})


@api.get("/api/career_jobs")
async def api_career_jobs():
    """Return cached career jobs so career.html can hydrate on load."""
    from career_workflow import JOBS_CACHE
    if not JOBS_CACHE.exists():
        return JSONResponse({"jobs": []})
    try:
        jobs = json.loads(JOBS_CACHE.read_text())
        return JSONResponse({"jobs": jobs if isinstance(jobs, list) else []})
    except Exception:
        return JSONResponse({"jobs": []})


def _cached_job(job_id: str) -> dict | None:
    """Look up one job from the career jobs cache by id."""
    from career_workflow import JOBS_CACHE
    if not JOBS_CACHE.exists():
        return None
    try:
        jobs = json.loads(JOBS_CACHE.read_text())
    except Exception:
        return None
    return next((j for j in jobs if j.get("id") == job_id), None)


@api.get("/api/career_resume/{job_id}")
async def api_career_resume(job_id: str):
    """Serve the per-job tailored resume PDF for inline preview + download."""
    from tools.resume_pdf import CAREER_RESUMES
    safe = Path(job_id).name
    pdf  = CAREER_RESUMES / f"{safe}.pdf"
    if not pdf.is_file():
        return JSONResponse({"error": "tailored resume not found"}, status_code=404)
    return FileResponse(str(pdf), media_type="application/pdf",
                        filename=f"tailored-resume-{safe}.pdf")


@api.post("/api/career_fill/{job_id}")
async def api_career_fill(job_id: str):
    """Open a cached job's application in a browser and auto-fill it.

    The browser is left open (logged in via the persistent profile) so Taran
    just reviews and clicks Submit. Broadcasts career_fill_started /
    career_fill_done / career_fill_error events so the frontend can drive
    the Open & Fill button's state from the actual fill lifecycle.
    """
    job = _cached_job(job_id)
    if not job:
        return JSONResponse({"ok": False, "error": "job not found in cache"}, status_code=404)
    if not (job.get("url") or "").startswith("http"):
        return JSONResponse({"ok": False, "error": "job has no application URL"}, status_code=400)

    async def _run():
        await broadcast({
            "type": "career_fill_started", "job_id": job_id,
            "company": job.get("company", ""),
        })
        try:
            from tools.playwright_apply import fill_application
            result = await asyncio.to_thread(fill_application, job, True)  # keep_open=True
            await broadcast({
                "type": "career_fill_done", "job_id": job_id,
                "company": job.get("company", ""),
                "platform": result.get("platform", ""),
                "fields_filled": len(result.get("fields_filled", [])),
                "needs_manual_login": bool(result.get("needs_manual_login")),
                "error": result.get("error", ""),
            })
        except Exception as e:
            log.warning("[career_fill] %s failed: %s", job_id, e)
            await broadcast({
                "type": "career_fill_error", "job_id": job_id,
                "error": str(e),
            })

    asyncio.create_task(_run())
    return JSONResponse({"ok": True, "company": job.get("company", ""), "platform": _detect_fill_platform(job.get("url", ""))})


@api.post("/api/career_bootstrap_login/{job_id}")
async def api_career_bootstrap_login(job_id: str):
    """Open the cached job's application URL in the persistent browser profile
    and leave the window open so Taran can sign in manually. Once he completes
    the login, cookies persist for the entire tenant — subsequent auto-fills
    against that Workday/ATS tenant will succeed without a login wall."""
    job = _cached_job(job_id)
    if not job:
        return JSONResponse({"ok": False, "error": "job not found in cache"}, status_code=404)
    url = (job.get("url") or "")
    if not url.startswith("http"):
        return JSONResponse({"ok": False, "error": "job has no application URL"}, status_code=400)

    async def _run():
        await broadcast({
            "type": "career_bootstrap_started", "job_id": job_id,
            "company": job.get("company", ""),
        })
        try:
            from tools.playwright_apply import open_for_bootstrap
            result = await asyncio.to_thread(open_for_bootstrap, url)
            await broadcast({
                "type": "career_bootstrap_done", "job_id": job_id,
                "ok": bool(result.get("ok")),
                "service": result.get("service", ""),
                "error": result.get("error", ""),
            })
        except Exception as e:
            log.warning("[career_bootstrap] %s failed: %s", job_id, e)
            await broadcast({
                "type": "career_bootstrap_error", "job_id": job_id,
                "error": str(e),
            })

    asyncio.create_task(_run())
    return JSONResponse({"ok": True})


def _detect_fill_platform(url: str) -> str:
    try:
        from tools.playwright_apply import _detect_platform
        return _detect_platform(url)
    except Exception:
        return "generic"


@api.get("/api/lessons")
async def api_lessons():
    if not LESSONS_FILE.exists():
        return JSONResponse({"lessons": "", "count": 0})
    text  = LESSONS_FILE.read_text(encoding="utf-8")
    count = text.count("## [")
    return JSONResponse({"lessons": text, "count": count})


@api.get("/api/vault_search")
async def api_vault_search(q: str = "", limit: int = 15):
    """Keyword search across all vault markdown files."""
    if not q.strip():
        return JSONResponse({"results": []})
    vault = BASE_VAULT
    if not vault.exists():
        return JSONResponse({"results": [], "error": "Vault not found"})

    terms = q.lower().split()
    results = []
    skip_dirs = {".obsidian", ".trash"}

    for md_file in vault.rglob("*.md"):
        if any(p in skip_dirs for p in md_file.parts):
            continue
        try:
            rel = str(md_file.relative_to(vault))
            text = md_file.read_text(encoding="utf-8", errors="ignore")
            text_lower = text.lower()
            if not all(t in text_lower for t in terms):
                continue
            idx = text_lower.find(terms[0])
            start, end = max(0, idx - 60), min(len(text), idx + 200)
            snippet = text[start:end].strip().replace("\n", " ")
            first_line = text.split("\n")[0].lstrip("# ").strip()
            results.append({"path": rel, "title": first_line or md_file.stem, "snippet": snippet})
            if len(results) >= limit:
                break
        except Exception:
            continue
    return JSONResponse({"results": results})


@api.get("/api/schedules")
async def api_schedules_list():
    return JSONResponse({"schedules": [schedule_to_dict(t) for t in load_schedules()]})


@api.post("/api/schedules")
async def api_schedules_create(body: dict):
    try:
        task = create_schedule(
            name=body.get("name", ""),
            text=body.get("text", ""),
            agent=body.get("agent", "general"),
            schedule_type=body.get("schedule_type", "daily"),
            schedule_time=body.get("schedule_time", "08:00"),
        )
        await broadcast({"type": "schedule_created", "schedule": schedule_to_dict(task)})
        return JSONResponse({"ok": True, "schedule": schedule_to_dict(task)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@api.delete("/api/schedules/{task_id}")
async def api_schedules_delete(task_id: str):
    ok = delete_schedule(task_id)
    if ok:
        await broadcast({"type": "schedule_deleted", "id": task_id})
    return JSONResponse({"ok": ok})


@api.patch("/api/schedules/{task_id}")
async def api_schedules_toggle(task_id: str, body: dict):
    enabled = body.get("enabled", True)
    ok = toggle_schedule(task_id, enabled)
    if ok:
        tasks = load_schedules()
        for t in tasks:
            if t.id == task_id:
                await broadcast({"type": "schedule_updated", "schedule": schedule_to_dict(t)})
                break
    return JSONResponse({"ok": ok})


@api.post("/api/dispatch")
async def api_dispatch(body: dict):
    """A2A endpoint — any agent subprocess can POST here to spawn another agent."""
    agent = body.get("agent", "general")
    task  = body.get("task", "").strip()
    if not task:
        return JSONResponse({"ok": False, "error": "task required"}, status_code=400)
    valid = {"career","general","finance","briefing","study","content","vault_curator","coding"}
    if agent not in valid:
        agent = "general"
    tid  = _make_id()
    item = {"id": tid, "text": task}
    q_map = {
        "career": _career_q, "finance": _finance_q, "briefing": _briefing_q,
        "study": _study_q, "content": _content_q,
        "vault_curator": _vault_curator_q, "coding": _coding_q,
    }
    await q_map.get(agent, _general_q).put(item)
    await broadcast({
        "type": "task_dispatched", "id": tid, "agent": agent,
        "text": f"🔗 A2A → {agent}: {task[:80]}",
    })
    await broadcast({
        "type": "agent_work", "agent": "system",
        "action": "dispatch", "icon": "🚀",
        "label": f"Agent-to-agent: {agent} spawned",
        "detail": task[:80],
        "ts": datetime.datetime.now().isoformat(),
    })
    log.info("[A2A] dispatch → %s: %s", agent, task[:60])
    return JSONResponse({"ok": True, "id": tid, "agent": agent})


@api.get("/api/activity")
async def api_activity(limit: int = 50, agent: str = "all"):
    entries = list(reversed(_activity_log))
    if agent != "all":
        entries = [e for e in entries if e.get("agent") == agent]
    return JSONResponse({"activity": entries[:limit]})


@api.get("/api/credentials")
async def api_credentials_list():
    try:
        from tools import credentials
        return JSONResponse({"credentials": credentials.list_services()})
    except Exception as e:
        return JSONResponse({"status": "error", "error": str(e)}, status_code=500)


@api.post("/api/credentials")
async def api_credentials_add(payload: dict):
    service = (payload.get("service") or "").strip()
    account = (payload.get("account") or "").strip()
    password = payload.get("password") or ""
    if not (service and account and password):
        return JSONResponse({"status": "error", "error": "service, account, password required"}, status_code=400)
    try:
        from tools import credentials
        credentials.store(service, account, password, notes=payload.get("notes"))
        return JSONResponse({"status": "ok"})
    except Exception as e:
        return JSONResponse({"status": "error", "error": str(e)}, status_code=500)


@api.delete("/api/credentials/{service}")
async def api_credentials_delete(service: str, account: str | None = None):
    try:
        from tools import credentials
        ok = credentials.delete(service, account)
        return JSONResponse({"status": "ok" if ok else "not_found"})
    except Exception as e:
        return JSONResponse({"status": "error", "error": str(e)}, status_code=500)


@api.get("/api/rag_stats")
async def api_rag_stats():
    try:
        from tools.rag import get_stats
        return JSONResponse(get_stats())
    except Exception as e:
        return JSONResponse({"status": "error", "error": str(e)})


@api.get("/api/rag_search")
async def api_rag_search(q: str = "", n: int = 4):
    if not q.strip():
        return JSONResponse({"results": [], "context": ""})
    try:
        from tools.rag import search
        ctx = await asyncio.to_thread(search, q, n)
        return JSONResponse({"context": ctx, "has_results": bool(ctx)})
    except Exception as e:
        return JSONResponse({"context": "", "error": str(e)})


@api.post("/api/rag_index")
async def api_rag_index(body: dict = {}):
    """Trigger a full vault re-index (runs in background)."""
    force = body.get("force", False)
    async def _run():
        from tools.rag import index_vault
        result = await asyncio.to_thread(index_vault, force)
        await broadcast({"type": "rag_index_done", "result": result})
    asyncio.create_task(_run())
    return JSONResponse({"ok": True, "message": "Indexing started in background"})


@api.get("/api/applications")
async def api_applications():
    from tools.tracker import load_applications, get_stats
    return JSONResponse({"applications": load_applications(), "stats": get_stats()})


@api.patch("/api/applications/{app_id}")
async def api_update_application(app_id: str, body: dict):
    from tools.tracker import update_status
    status = body.get("status", "")
    ok = update_status(app_id, status)
    if ok:
        await broadcast({"type": "application_updated", "id": app_id, "status": status})
    return JSONResponse({"ok": ok})


@api.get("/api/logs")
async def api_logs(lines: int = 100, level: str = "all"):
    """Return recent log lines. level=all|error|warning"""
    entries = []
    for log_path in (_LOG_FILE, Path(__file__).parent / "server.log"):
        if not log_path.exists():
            continue
        try:
            text = log_path.read_text(encoding="utf-8", errors="ignore")
            all_lines = text.splitlines()
            for line in all_lines:
                if level == "error" and " ERROR " not in line and "Traceback" not in line and "Error" not in line:
                    continue
                if level == "warning" and " WARNING " not in line and " ERROR " not in line:
                    continue
                entries.append(line)
            break  # only read first found file
        except Exception:
            continue
    return JSONResponse({"lines": entries[-lines:]})


# ── Trades & bankroll API ─────────────────────────────────────────────────────

@api.get("/api/trades")
async def api_get_trades():
    from tools.trade_tracker import get_all_trades
    return JSONResponse(get_all_trades())


@api.post("/api/trades")
async def api_add_trade(body: dict):
    from tools.trade_tracker import add_trade
    try:
        trade = add_trade(
            asset        = body.get("asset", ""),
            direction    = body.get("direction", "LONG"),
            entry_price  = float(body.get("entry_price", 0)),
            stop_loss    = float(body["stop_loss"]) if body.get("stop_loss") else None,
            take_profit  = [float(p) for p in body.get("take_profit", []) if p],
            risk_pct     = float(body.get("risk_pct", 1.0)),
            leverage     = int(body.get("leverage", 1)),
            signal_text  = body.get("signal_text", ""),
            source       = body.get("source", "manual"),
        )
        await broadcast({"type": "trade_added", "trade": trade})
        return JSONResponse({"ok": True, "trade": trade})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@api.patch("/api/trades/{trade_id}")
async def api_update_trade(trade_id: str, body: dict):
    from tools.trade_tracker import update_trade_pnl
    pnl   = body.get("pnl")
    exit_ = body.get("exit_price")
    notes = body.get("notes", "")
    if pnl is None:
        return JSONResponse({"ok": False, "error": "pnl required"}, status_code=400)
    trade = update_trade_pnl(trade_id, float(pnl), float(exit_) if exit_ else None, notes)
    if not trade:
        return JSONResponse({"ok": False, "error": "trade not found"}, status_code=404)
    await broadcast({"type": "trade_updated", "trade": trade})
    return JSONResponse({"ok": True, "trade": trade})


@api.post("/api/trades/{trade_id}/close")
async def api_close_trade(trade_id: str, body: dict = {}):
    from tools.trade_tracker import close_trade, get_bankroll
    pnl   = float(body.get("pnl", 0))
    exit_ = body.get("exit_price")
    notes = body.get("notes", "")
    trade = close_trade(trade_id, pnl, float(exit_) if exit_ else None, notes)
    if not trade:
        return JSONResponse({"ok": False, "error": "trade not found"}, status_code=404)
    br = get_bankroll()
    await broadcast({"type": "trade_closed", "trade": trade, "bankroll": br})
    return JSONResponse({"ok": True, "trade": trade, "bankroll": br})


@api.post("/api/trades/{trade_id}/cancel")
async def api_cancel_trade(trade_id: str):
    from tools.trade_tracker import cancel_trade
    ok = cancel_trade(trade_id)
    if ok:
        await broadcast({"type": "trade_cancelled", "id": trade_id})
    return JSONResponse({"ok": ok})


@api.get("/api/signal_log")
async def api_signal_log():
    """Return parsed entries from the Dr. Profit live signals vault log."""
    from dr_profit_monitor import SIGNALS_LOG
    if not SIGNALS_LOG.exists():
        return JSONResponse({"entries": []})
    try:
        text = SIGNALS_LOG.read_text(encoding="utf-8")
        entries = []
        for block in text.split("## ["):
            if not block.strip():
                continue
            lines = block.strip().splitlines()
            ts_line = lines[0].strip().rstrip("]") if lines else ""
            ts = ts_line.split("]")[0] if "]" in ts_line else ts_line[:16]
            asset_dir = ts_line.split("] ")[-1].strip() if "] " in ts_line else ""
            parts = asset_dir.split()
            asset     = parts[0] if parts else "?"
            direction = parts[1] if len(parts) > 1 else "?"
            entry = sl = None
            for l in lines:
                if "Entry:" in l:
                    try: entry = float(l.split(":")[-1].strip())
                    except: pass
                if "SL:" in l:
                    try: sl = float(l.split(":")[-1].strip())
                    except: pass
            entries.append({
                "ts": ts, "asset": asset, "direction": direction,
                "entry": entry, "stop_loss": sl,
                "brief": "\n".join(lines[1:5]),
            })
        return JSONResponse({"entries": entries[-50:]})
    except Exception as e:
        return JSONResponse({"entries": [], "error": str(e)})


# ── File uploads ─────────────────────────────────────────────────────────────

import mimetypes

_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB

@api.post("/api/upload")
async def api_upload(file: UploadFile = File(...)):
    """Save an uploaded file and return its path + metadata.

    Streamed to disk in 1 MB chunks with a hard size cap — reading the whole
    body into memory at once would let a large upload OOM the process.
    """
    # Strip directory components from client-supplied filename to prevent path traversal
    base_name = Path(file.filename or "upload").name or "upload"
    safe      = f"{uuid.uuid4().hex[:8]}_{base_name}"
    dest      = UPLOADS_DIR / safe

    size = 0
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > _MAX_UPLOAD_BYTES:
                    out.close()
                    dest.unlink(missing_ok=True)
                    return JSONResponse(
                        {"ok": False,
                         "error": f"File exceeds {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit"},
                        status_code=413,
                    )
                out.write(chunk)
    except Exception as e:
        dest.unlink(missing_ok=True)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    mime      = file.content_type or mimetypes.guess_type(base_name)[0] or "application/octet-stream"
    is_image  = mime.startswith("image/")
    return JSONResponse({
        "ok":        True,
        "name":      base_name,
        "path":      str(dest),
        "url":       f"/api/uploads/{safe}",
        "mime":      mime,
        "is_image":  is_image,
        "size":      size,
    })


@api.get("/api/uploads/{filename}")
async def api_serve_upload(filename: str):
    """Serve an uploaded file (for image previews)."""
    # Resolve and confirm the path is inside UPLOADS_DIR — defense-in-depth against traversal
    safe_name = Path(filename).name
    path = (UPLOADS_DIR / safe_name).resolve()
    if not path.is_file() or UPLOADS_DIR.resolve() not in path.parents:
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(str(path))


# ── Content renders ──────────────────────────────────────────────────────────

@api.get("/api/renders")
async def api_renders():
    """List rendered videos per channel, newest first — feeds the Content page."""
    channels = []
    for key, cfg in RENDER_CHANNELS.items():
        videos = []
        folder = cfg["dir"]
        if folder.is_dir():
            files = []
            for f in folder.glob("*.mp4"):
                try:
                    files.append((f, f.stat()))
                except OSError:
                    continue
            for f, st in sorted(files, key=lambda x: x[1].st_mtime, reverse=True):
                videos.append({
                    "name":  f.name,
                    "title": f.stem,
                    "size":  st.st_size,
                    "mtime": st.st_mtime,
                    "url":   f"/api/render_file/{key}/{f.name}",
                })
        channels.append({
            "key":    key,
            "name":   cfg["name"],
            "desc":   cfg["desc"],
            "icon":   cfg["icon"],
            "tiktok": cfg["tiktok"],
            "count":  len(videos),
            "videos": videos,
        })
    return JSONResponse({"channels": channels})


@api.get("/api/render_file/{channel}/{filename}")
async def api_render_file(channel: str, filename: str, dl: int = 0):
    """Serve a rendered video. FileResponse handles Range requests so the
    dashboard <video> players can seek; pass ?dl=1 to force a download."""
    cfg = RENDER_CHANNELS.get(channel)
    if not cfg:
        return JSONResponse({"error": "unknown channel"}, status_code=404)
    # Resolve and confirm the path stays inside the channel folder (anti-traversal)
    safe_name = Path(filename).name
    base = cfg["dir"].resolve()
    path = (base / safe_name).resolve()
    if not path.is_file() or base not in path.parents or path.suffix.lower() != ".mp4":
        return JSONResponse({"error": "not found"}, status_code=404)
    headers = {}
    if dl:
        headers["Content-Disposition"] = f'attachment; filename="{safe_name}"'
    return FileResponse(str(path), media_type="video/mp4", headers=headers)


# ── Activity log ─────────────────────────────────────────────────────────────

def _load_activity():
    global _activity_log
    if ACTIVITY_FILE.exists():
        try:
            _activity_log = json.loads(ACTIVITY_FILE.read_text())[-_MAX_ACTIVITY:]
        except Exception:
            _activity_log = []

def _save_activity():
    try:
        ACTIVITY_FILE.write_text(json.dumps(_activity_log[-_MAX_ACTIVITY:]))
    except Exception as e:
        log.warning("Failed to save activity: %s", e)

async def _log_activity(tid: str, agent: str, task: str, result: str):
    entry = {
        "id":     tid,
        "agent":  agent,
        "task":   task,
        "result": result,
        "ts":     datetime.datetime.now().isoformat(),
    }
    _activity_log.append(entry)
    if len(_activity_log) > _MAX_ACTIVITY:
        del _activity_log[:-_MAX_ACTIVITY]
    _save_activity()
    await broadcast({"type": "activity_update", "entry": entry})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_id() -> str:
    # Millisecond timestamp + short uuid suffix — collision-free even within the same ms
    ts = int(datetime.datetime.now().timestamp() * 1000)
    return f"task_{ts}_{uuid.uuid4().hex[:6]}"

def _current_week() -> str:
    return datetime.date.today().strftime("%Y-W%W")

def _load_usage():
    global _weekly_tokens, _session_tasks_done, _weekly_by_agent
    if USAGE_FILE.exists():
        try:
            data = json.loads(USAGE_FILE.read_text())
            if data.get("week") == _current_week():
                _weekly_tokens = data.get("tokens", 0)
                _weekly_by_agent = data.get("by_agent", {}) or {}
            _session_tasks_done = data.get("tasks_done", 0)
        except Exception:
            pass

_last_usage_save: float = 0.0

def _save_usage(force: bool = False):
    """Persist usage stats. Debounced to one write per 5 s — `_accumulate_tokens`
    fires on every streamed result, and usage.json is best-effort stats, not
    state we can't afford to lose. Discrete events (task completion) pass
    force=True to guarantee an immediate write."""
    global _last_usage_save
    now = datetime.datetime.now().timestamp()
    if not force and now - _last_usage_save < 5.0:
        return
    _last_usage_save = now
    try:
        USAGE_FILE.write_text(json.dumps({
            "week": _current_week(), "tokens": _weekly_tokens,
            "tasks_done": _session_tasks_done,
            "by_agent": _weekly_by_agent,
        }))
    except Exception as e:
        log.warning("Failed to save usage: %s", e)

def _esc(text: str) -> str:
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text

async def _safe_tg(fn, text: str):
    try:
        await fn(text)
    except Exception as e:
        log.warning("TG send: %s", e)


# ── Entry ─────────────────────────────────────────────────────────────────────

async def _evict_stale_verifications():
    """Drop pending verifications/requeues older than 24 h so the dicts can't grow forever."""
    while True:
        await asyncio.sleep(3600)  # check hourly
        try:
            cutoff = datetime.datetime.now().timestamp() - 86400
            stale = [tid for tid, v in _pending_verifications.items()
                     if v.get("ts", 0) < cutoff]
            for tid in stale:
                _pending_verifications.pop(tid, None)
            # _pending_requeues is keyed by the correction task's id (not the
            # verification id), so it must be evicted by its own age — if a
            # correction task errors before verifying, its entry would
            # otherwise leak forever.
            stale_rq = [cid for cid, r in _pending_requeues.items()
                        if r.get("ts", 0) < cutoff]
            for cid in stale_rq:
                _pending_requeues.pop(cid, None)
            if stale or stale_rq:
                log.info("[cleanup] Evicted %d verifications, %d requeues",
                         len(stale), len(stale_rq))
        except Exception as e:
            log.warning("[cleanup] eviction error: %s", e)


def _accumulate_tokens(agent: str, usage: dict):
    """Usage callback wired to tools.llm — runs for EVERY agent, not just general."""
    global _session_tokens, _weekly_tokens
    input_tok  = usage.get("input_tokens", 0)
    output_tok = usage.get("output_tokens", 0)
    tokens = input_tok + output_tok
    if not tokens:
        return
    _session_tokens += tokens
    _weekly_tokens  += tokens

    bucket = _weekly_by_agent.setdefault(agent or "general", {
        "input": 0, "output": 0, "cache_creation": 0, "cache_read": 0,
        "cost_usd": 0.0, "calls": 0, "model": ""
    })
    bucket["input"]          += input_tok
    bucket["output"]         += output_tok
    bucket["cache_creation"] += usage.get("cache_creation_input_tokens", 0)
    bucket["cache_read"]     += usage.get("cache_read_input_tokens", 0)
    bucket["cost_usd"]       += usage.get("cost_usd", 0.0)
    bucket["calls"]          += 1
    if usage.get("model"):
        bucket["model"] = usage["model"]

    _save_usage()
    # Schedule the broadcast on the running loop (callback is invoked from inside it)
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(broadcast({
            "type": "usage_update",
            "session_tokens": _session_tokens,
            "weekly_tokens":  _weekly_tokens,
            "agent": agent,
            "by_agent": _weekly_by_agent,
        }))
        # Refresh subscription quota after each task (throttled inside fetch_quota)
        loop.create_task(_refresh_quota_broadcast())
    except RuntimeError:
        pass


async def _refresh_quota_broadcast():
    """Fetch the Claude subscription quota off-thread and push it to the dashboard."""
    try:
        from tools.usage_quota import fetch_quota
        loop = asyncio.get_running_loop()
        quota = await loop.run_in_executor(None, fetch_quota)
        if quota:
            await broadcast({"type": "quota_update", "quota": quota})
    except Exception as e:
        log.warning("quota refresh failed: %s", e)


async def main():
    from telegram_bot import build_app, task_queue, send_message
    import tools.approval as ag
    from tools.llm import register_usage_callback
    _load_usage()
    _load_activity()
    register_usage_callback(_accumulate_tokens)

    try:
        from tools import credentials
        credentials.unlock()
    except Exception as e:
        log.warning("Credential keychain unlock failed: %s (run scripts/setup_keychain.sh)", e)

    tg_app = build_app()

    async def approval_sender(action_id: str, text: str, action: str = "approve"):
        from telegram_bot import send_approval_request, send_routing_request
        if action == "route":
            await send_routing_request(action_id, text)
        else:
            await send_approval_request(action_id, _esc(text))
        await broadcast({"type": "approval_request", "id": action_id, "text": text, "action": action})

    ag.register_sender(approval_sender)

    server = uvicorn.Server(
        uvicorn.Config(api, host="0.0.0.0", port=DASHBOARD_PORT, log_level="warning")
    )

    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling(drop_pending_updates=True)

    async def tg_intake():
        while True:
            text = await task_queue.get()
            try:
                await _handle_new_task(text)
            except Exception as e:
                log.exception("TG intake: %s", e)
            finally:
                task_queue.task_done()

    workers = [
        asyncio.create_task(server.serve(),                             name="uvicorn"),
        asyncio.create_task(tg_intake(),                                name="tg_intake"),
        asyncio.create_task(_worker(_career_q,        "career"),        name="career_worker"),
        asyncio.create_task(_worker(_general_q,        "general"),      name="general_worker"),
        asyncio.create_task(_worker(_finance_q,       "finance"),       name="finance_worker"),
        asyncio.create_task(_worker(_briefing_q,      "briefing"),      name="briefing_worker"),
        asyncio.create_task(_worker(_study_q,         "study"),         name="study_worker"),
        asyncio.create_task(_worker(_content_q,       "content"),       name="content_worker"),
        asyncio.create_task(_worker(_vault_curator_q, "vault_curator"), name="vault_curator_worker"),
        asyncio.create_task(_worker(_coding_q,        "coding"),        name="coding_worker"),
        asyncio.create_task(_start_dr_profit_monitor(),                name="dr_profit_monitor"),
        asyncio.create_task(ag.watch_approvals(),                       name="approval_watcher"),
        asyncio.create_task(_retry_worker(),                            name="retry_worker"),
        asyncio.create_task(run_scheduler(_schedule_dispatch),          name="scheduler"),
        asyncio.create_task(_evict_stale_verifications(),               name="evict_cleanup"),
    ]

    try:
        await asyncio.gather(*workers)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        for w in workers:
            w.cancel()
        server.should_exit = True
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
