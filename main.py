"""
Agentic OS — entry point.
"""

import asyncio
import datetime
import json
import logging
import subprocess
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from config import DASHBOARD_PORT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# ── Global State ────────────────────────────────────────────────────────────

_ws_clients: list[WebSocket] = []
_session_id: str | None = None
_session_tokens: int = 0
_weekly_tokens: int = 0
_session_tasks_done: int = 0
_pending_interactions: dict[str, asyncio.Future] = {}

USAGE_FILE = Path(__file__).parent / "usage.json"

# ── FastAPI app ─────────────────────────────────────────────────────────────

api = FastAPI(title="Agentic OS")

DASHBOARD_HTML = Path(__file__).parent / "dashboard" / "index.html"
CAREER_HTML = Path(__file__).parent / "dashboard" / "career.html"
PERSONAL_HTML = Path(__file__).parent / "dashboard" / "personal.html"
REVIEW_HTML = Path(__file__).parent / "dashboard" / "review.html"

@api.get("/")
async def serve_dashboard(): return HTMLResponse(DASHBOARD_HTML.read_text())
@api.get("/career")
async def serve_career(): return HTMLResponse(CAREER_HTML.read_text())
@api.get("/personal")
async def serve_personal(): return HTMLResponse(PERSONAL_HTML.read_text())
@api.get("/review")
async def serve_review(): return HTMLResponse(REVIEW_HTML.read_text())

# ── WebSocket broadcast ──────────────────────────────────────────────────────

async def broadcast(event: dict):
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in _ws_clients: _ws_clients.remove(ws)

@api.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.append(ws)
    try:
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)
            t = msg.get("type")
            
            if t == "task":
                from telegram_bot import task_queue
                await task_queue.put(msg["text"])
                await broadcast({"type": "task_queued", "text": msg["text"]})
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
                ag.resolve(msg["id"], True)
                await broadcast({"type": "approved", "id": msg["id"]})
            elif t == "deny":
                import tools.approval as ag
                ag.resolve(msg["id"], False)
                await broadcast({"type": "denied", "id": msg["id"]})
            elif t == "task_interact":
                tid = msg.get("id")
                if tid in _pending_interactions:
                    _pending_interactions[tid].set_result(msg)
            elif t == "route":
                import tools.approval as ag
                ag.resolve(msg["id"], msg["choice"])
                await broadcast({"type": "routed", "id": msg["id"], "choice": msg["choice"]})
    except WebSocketDisconnect:
        if ws in _ws_clients: _ws_clients.remove(ws)
    except Exception as e:
        log.warning(f"WS error: {e}")

# ── API Endpoints ───────────────────────────────────────────────────────────

@api.get("/api/status")
async def api_status():
    from orchestrator import is_running
    from telegram_bot import task_queue
    from tools.llm import get_preferred_provider
    import tools.approval as ag
    from personal_tasks_workflow import is_running as p_run
    from code_review_workflow import is_running as r_run
    from career_workflow import is_running as c_run

    return {
        "running": is_running() or p_run() or r_run() or c_run(),
        "queued": task_queue.qsize(),
        "pending_approvals": ag.pending_ids(),
        "session_tokens": _session_tokens,
        "weekly_tokens": _weekly_tokens,
        "tasks_done": _session_tasks_done,
        "provider": get_preferred_provider(),
    }

# ── Utils ───────────────────────────────────────────────────────────────────

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
        except Exception: pass

def _save_usage():
    try:
        USAGE_FILE.write_text(json.dumps({
            "week": _current_week(), 
            "tokens": _weekly_tokens,
            "tasks_done": _session_tasks_done
        }))
    except Exception as e:
        log.warning("Failed to save usage: %s", e)

def _escape_md(text: str) -> str:
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text

async def _tg_safe(send_telegram, text: str):
    try: await send_telegram(text)
    except Exception as e: log.warning("Telegram send failed: %s", e)

async def _wait_for_interaction(task_id: str):
    fut = asyncio.get_running_loop().create_future()
    _pending_interactions[task_id] = fut
    try:
        return await fut
    finally:
        _pending_interactions.pop(task_id, None)

# ── Orchestrator worker ──────────────────────────────────────────────────────

async def handle_queued_task(task, send_telegram, broadcast):
    global _session_id, _session_tokens, _weekly_tokens, _session_tasks_done
    from orchestrator import run_task
    import tools.approval as ag

    log.info("Processing task: %s", task)
    await broadcast({"type": "task_started", "text": task})
    task_id = f"task_{int(datetime.datetime.now().timestamp())}"
    current_prompt = task
    last_result = ""

    try:
        # ── Step 1: Routing ─────────────────────────────────────────────
        from tools.approval import ask_routing
        category = await ask_routing(task)
        
        while True:
            if category in ["career", "personal", "review"]:
                await _tg_safe(send_telegram, f"Delegating to *{category.title()}* workflow...")
                if category == "career":
                    from career_workflow import run_career_search
                    await run_career_search(current_prompt, broadcast, send_telegram)
                elif category == "personal":
                    from personal_tasks_workflow import run_personal_task
                    await run_personal_task(current_prompt, broadcast, send_telegram)
                elif category == "review":
                    from code_review_workflow import run_code_review
                    await run_code_review(current_prompt, broadcast, send_telegram)
                
                last_result = f"Completed {category.title()} workflow."
                category = "general" # Switch to general for follow-ups
            else:
                # General Orchestrator
                await _tg_safe(send_telegram, f"Running Orchestrator: _{_escape_md(current_prompt)}_")
                result, _session_id, usage = await run_task(
                    current_prompt, send_telegram=send_telegram,
                    session_id=_session_id, broadcast=broadcast,
                )
                last_result = result
                
                tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                _session_tokens += tokens
                _weekly_tokens += tokens
                _save_usage()
                await broadcast({"type": "usage_update", "session_tokens": _session_tokens, "weekly_tokens": _weekly_tokens})

            # ── Step 2: Verification Loop ───────────────────────────────
            await broadcast({
                "type": "task_verification_required",
                "id": task_id,
                "text": last_result,
                "prompt": current_prompt
            })
            await _tg_safe(send_telegram, f"Task completed. Awaiting verification...\n\nResult preview: {last_result[:200]}...")

            interaction = await _wait_for_interaction(task_id)
            
            if interaction.get("action") == "complete":
                from tools.logger import log_completed_task
                log_completed_task(task[:40], f"Original Task: {task}\n\nFinal Result: {last_result}", actions=["User verified and completed."])
                
                _session_tasks_done += 1
                _save_usage()
                await broadcast({"type": "usage_update", "tasks_done": _session_tasks_done})
                await broadcast({"type": "task_done", "id": task_id, "text": last_result})
                await _tg_safe(send_telegram, f"✅ Task verified and completed.")
                break
            elif interaction.get("action") == "followup":
                current_prompt = interaction.get("text")
                await _tg_safe(send_telegram, f"Follow-up received: _{_escape_md(current_prompt)}_")
                continue # Loop back
            else:
                break

    except Exception as e:
        log.exception("Task failed: %s", task)
        await broadcast({"type": "task_error", "id": task_id, "text": str(e)})

async def orchestrator_worker(task_queue, send_telegram):
    while True:
        task = await task_queue.get()
        try:
            await handle_queued_task(task, send_telegram, broadcast)
        except Exception as e:
            log.exception("Worker error: %s", e)
        finally:
            task_queue.task_done()

# ── Main ─────────────────────────────────────────────────────────────────────

async def main():
    from telegram_bot import build_app, task_queue, send_message
    import tools.approval as ag
    _load_usage()

    tg_app = build_app()

    async def approval_sender(action_id: str, text: str, action: str = "approve"):
        from telegram_bot import send_approval_request, send_routing_request
        if action == "route": await send_routing_request(action_id, text)
        else: await send_approval_request(action_id, _escape_md(text))
        await broadcast({"type": "approval_request", "id": action_id, "text": text, "action": action})

    ag.register_sender(approval_sender)

    uvicorn_config = uvicorn.Config(api, host="127.0.0.1", port=DASHBOARD_PORT, log_level="warning")
    uvicorn_server = uvicorn.Server(uvicorn_config)

    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling(drop_pending_updates=True)

    tasks = [
        asyncio.create_task(uvicorn_server.serve(), name="uvicorn"),
        asyncio.create_task(orchestrator_worker(task_queue, send_message), name="worker"),
        asyncio.create_task(ag.watch_approvals(), name="approval_watcher"),
    ]

    try:
        await asyncio.gather(*tasks)
    except (KeyboardInterrupt, asyncio.CancelledError): pass
    finally:
        for t in tasks: t.cancel()
        uvicorn_server.should_exit = True
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
