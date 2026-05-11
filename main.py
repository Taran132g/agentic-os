"""
Agentic OS — entry point.
True concurrent multi-agent: four independent workers run in parallel.
Each agent (career / personal / review / general) has its own queue.
Routing is non-blocking — new tasks can be submitted while others run.
"""

import asyncio
import datetime
import json
import logging
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from config import DASHBOARD_PORT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# ── Global State ─────────────────────────────────────────────────────────────

_ws_clients: list[WebSocket] = []
_session_tokens: int = 0
_weekly_tokens: int = 0
_session_tasks_done: int = 0
_pending_interactions: dict[str, asyncio.Future] = {}

# Per-agent queues — each has its own independent worker
_career_q:   asyncio.Queue = asyncio.Queue()
_personal_q: asyncio.Queue = asyncio.Queue()
_review_q:   asyncio.Queue = asyncio.Queue()
_general_q:  asyncio.Queue = asyncio.Queue()

USAGE_FILE = Path(__file__).parent / "usage.json"
BASE_VAULT = Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain"
TASKS_DIR  = BASE_VAULT / "Jarvis Hub" / "Tasks"

# ── FastAPI ───────────────────────────────────────────────────────────────────

api  = FastAPI(title="Agentic OS")
_DASH = Path(__file__).parent / "dashboard"


@api.get("/")
async def srv_dash():     return HTMLResponse((_DASH / "index.html").read_text())
@api.get("/career")
async def srv_career():   return HTMLResponse((_DASH / "career.html").read_text())
@api.get("/personal")
async def srv_personal(): return HTMLResponse((_DASH / "personal.html").read_text())
@api.get("/review")
async def srv_review():   return HTMLResponse((_DASH / "review.html").read_text())


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

            if t == "task":
                await _handle_new_task(msg["text"])

            elif t == "career_start":
                tid = _make_id()
                keywords = msg.get("keywords", "software engineering")
                await _career_q.put({"id": tid, "text": keywords})
                await broadcast({"type": "task_dispatched", "id": tid, "agent": "career",
                                 "text": keywords})

            elif t == "personal_start":
                tid = _make_id()
                await _personal_q.put({"id": tid, "text": msg.get("task", "")})
                await broadcast({"type": "task_dispatched", "id": tid, "agent": "personal",
                                 "text": msg.get("task", "")})

            elif t == "review_start":
                tid    = _make_id()
                target = msg.get("path") or "current changes"
                await _review_q.put({"id": tid, "text": target})
                await broadcast({"type": "task_dispatched", "id": tid, "agent": "review", "text": target})

            elif t == "route":
                tid    = msg.get("id")
                choice = msg.get("choice")
                import tools.approval as ag
                ag.resolve(tid, choice)
                await broadcast({"type": "routed", "id": tid, "choice": choice})

            elif t == "stop":
                from tools.llm import stop_active_proc
                if stop_active_proc():
                    await broadcast({"type": "task_error", "text": "Task stopped by user."})

            elif t == "set_provider":
                from tools.llm import set_preferred_provider
                set_preferred_provider(msg["provider"])
                await broadcast({"type": "provider_updated", "provider": msg["provider"]})

            elif t == "approve":
                import tools.approval as ag
                ag.resolve(msg["id"], "approved")
                await broadcast({"type": "approved", "id": msg["id"]})

            elif t == "deny":
                import tools.approval as ag
                ag.resolve(msg["id"], "denied")
                await broadcast({"type": "denied", "id": msg["id"]})

            elif t == "task_interact":
                tid = msg.get("id")
                if tid in _pending_interactions and not _pending_interactions[tid].done():
                    _pending_interactions[tid].set_result(msg)

    except WebSocketDisconnect:
        if ws in _ws_clients:
            _ws_clients.remove(ws)
    except Exception as e:
        log.warning("WS error: %s", e)


# ── Routing ───────────────────────────────────────────────────────────────────

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
    if   choice == "career":   await _career_q.put(item)
    elif choice == "personal": await _personal_q.put(item)
    elif choice == "review":   await _review_q.put(item)
    else:                      await _general_q.put(item)

    await broadcast({"type": "task_dispatched", "id": tid, "agent": choice, "text": text})


# ── Agent task runner ─────────────────────────────────────────────────────────

async def _run_agent_task(item: dict, agent: str):
    global _session_tasks_done, _session_tokens, _weekly_tokens
    tid  = item["id"]
    text = item["text"]

    log.info("[%s] ▶ %s", agent, text[:70])
    await broadcast({"type": "task_started", "id": tid, "text": text, "agent": agent})

    from telegram_bot import send_message as tg
    current_prompt = text
    result         = ""

    try:
        while True:
            if agent == "career":
                from career_workflow import run_career_search
                await run_career_search(current_prompt, broadcast, tg)
                result = "Career search workflow complete."
            elif agent == "personal":
                from personal_tasks_workflow import run_personal_task
                await run_personal_task(current_prompt, broadcast, tg)
                result = "Personal task workflow complete."
            elif agent == "review":
                from code_review_workflow import run_code_review
                await run_code_review(current_prompt, broadcast, tg)
                result = "Code review workflow complete."
            else:
                from orchestrator import run_task
                result, _, usage = await run_task(current_prompt, send_telegram=tg, broadcast=broadcast)
                tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                _session_tokens += tokens
                _weekly_tokens  += tokens
                _save_usage()
                await broadcast({"type": "usage_update",
                                 "session_tokens": _session_tokens, "weekly_tokens": _weekly_tokens})

            # Show verification card
            await broadcast({"type": "task_verification_required",
                             "id": tid, "text": result, "prompt": current_prompt})
            await _safe_tg(tg, f"Task ready — tap Complete or send follow-up.")

            interaction = await _wait_for_interaction(tid)

            if interaction.get("action") == "followup":
                current_prompt = interaction.get("text", "")
                await _safe_tg(tg, f"Follow-up: {current_prompt[:80]}")
                continue

            # Completed
            from tools.logger import log_completed_task
            log_completed_task(
                text[:50],
                f"Agent: {agent}\nTask: {text}\nResult: {result[:400]}",
                actions=["User verified and completed."],
                task_type=agent,
            )
            _session_tasks_done += 1
            _save_usage()
            await broadcast({"type": "usage_update", "tasks_done": _session_tasks_done})
            await broadcast({"type": "task_done", "id": tid, "text": result, "agent": agent})
            await _safe_tg(tg, "✅ Task complete.")
            break

    except Exception as e:
        log.exception("[%s] task error", agent)
        await broadcast({"type": "task_error", "id": tid, "text": str(e), "agent": agent})


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


# ── API ───────────────────────────────────────────────────────────────────────

@api.get("/api/status")
async def api_status():
    from orchestrator import is_running as g_run
    from personal_tasks_workflow import is_running as p_run
    from code_review_workflow import is_running as r_run
    from career_workflow import is_running as c_run
    from tools.llm import get_preferred_provider
    import tools.approval as ag

    return {
        "running": g_run() or p_run() or r_run() or c_run(),
        "running_by_agent": {
            "career": c_run(), "personal": p_run(),
            "review": r_run(), "general": g_run(),
        },
        "queued": sum(q.qsize() for q in (_career_q, _personal_q, _review_q, _general_q)),
        "queued_by_agent": {
            "career": _career_q.qsize(), "personal": _personal_q.qsize(),
            "review": _review_q.qsize(), "general": _general_q.qsize(),
        },
        "pending_approvals": ag.pending_ids(),
        "session_tokens": _session_tokens,
        "weekly_tokens":  _weekly_tokens,
        "tasks_done":     _session_tasks_done,
        "provider":       get_preferred_provider(),
    }


@api.get("/api/history")
async def api_history(filter: str = "all"):
    if not TASKS_DIR.exists():
        return JSONResponse({"history": []})
    tasks = []
    for f in sorted(TASKS_DIR.glob("*.md"), reverse=True)[:60]:
        try:
            lines     = f.read_text(encoding="utf-8").split("\n")
            name      = lines[0].lstrip("# ").strip() if lines else f.stem
            status    = "COMPLETED"
            task_type = "general"
            for line in lines:
                if line.startswith("## Status:"):
                    status = line.replace("## Status:", "").strip()
                if line.startswith("## Type:"):
                    task_type = line.replace("## Type:", "").strip()

            # Heuristic fallback when type not in file
            if task_type == "general":
                if "Code Review" in name:  task_type = "review"
                elif "Career" in name:     task_type = "career"
                else:                      task_type = "personal"

            if filter != "all" and task_type != filter:
                continue

            tasks.append({"id": f.stem, "text": name,
                          "time": f.stem[:10] if len(f.stem) >= 10 else "", "status": status})
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_id() -> str:
    return f"task_{int(datetime.datetime.now().timestamp() * 1000)}"

def _current_week() -> str:
    return datetime.date.today().strftime("%Y-W%W")

def _load_usage():
    global _weekly_tokens, _session_tasks_done
    if USAGE_FILE.exists():
        try:
            data = json.loads(USAGE_FILE.read_text())
            if data.get("week") == _current_week():
                _weekly_tokens = data.get("tokens", 0)
            _session_tasks_done = data.get("tasks_done", 0)
        except Exception:
            pass

def _save_usage():
    try:
        USAGE_FILE.write_text(json.dumps({
            "week": _current_week(), "tokens": _weekly_tokens,
            "tasks_done": _session_tasks_done,
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

async def _wait_for_interaction(task_id: str):
    fut = asyncio.get_running_loop().create_future()
    _pending_interactions[task_id] = fut
    try:
        return await asyncio.wait_for(asyncio.shield(fut), timeout=600)
    except asyncio.TimeoutError:
        return {"action": "complete"}
    finally:
        _pending_interactions.pop(task_id, None)


# ── Entry ─────────────────────────────────────────────────────────────────────

async def main():
    from telegram_bot import build_app, task_queue, send_message
    import tools.approval as ag
    _load_usage()

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
        uvicorn.Config(api, host="127.0.0.1", port=DASHBOARD_PORT, log_level="warning")
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
        asyncio.create_task(server.serve(),                    name="uvicorn"),
        asyncio.create_task(tg_intake(),                       name="tg_intake"),
        asyncio.create_task(_worker(_career_q,   "career"),    name="career_worker"),
        asyncio.create_task(_worker(_personal_q, "personal"),  name="personal_worker"),
        asyncio.create_task(_worker(_review_q,   "review"),    name="review_worker"),
        asyncio.create_task(_worker(_general_q,  "general"),   name="general_worker"),
        asyncio.create_task(ag.watch_approvals(),              name="approval_watcher"),
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
