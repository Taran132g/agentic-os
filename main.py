"""
Agentic OS — entry point.

Runs three things concurrently:
  1. Telegram bot (polling)
  2. FastAPI dashboard (localhost:8000)
  3. Orchestrator worker (processes task queue)
  4. Approval watcher (polls for Claude approval requests)

Usage:
    python main.py
    Then open http://localhost:8000 in your browser.
    Or send /task <anything> to @Trading_Taran_bot on Telegram.
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

# ── FastAPI app ─────────────────────────────────────────────────────────────

api = FastAPI(title="Agentic OS")

DASHBOARD_HTML = Path(__file__).parent / "dashboard" / "index.html"
CAREER_HTML = Path(__file__).parent / "dashboard" / "career.html"


@api.get("/")
async def serve_dashboard():
    return HTMLResponse(DASHBOARD_HTML.read_text())


@api.get("/career")
async def serve_career():
    return HTMLResponse(CAREER_HTML.read_text())


# ── WebSocket broadcast ──────────────────────────────────────────────────────

_ws_clients: list[WebSocket] = []


async def broadcast(event: dict):
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.remove(ws)


@api.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.append(ws)
    try:
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)
            if msg.get("type") == "task":
                from telegram_bot import task_queue
                await task_queue.put(msg["text"])
                await broadcast({"type": "task_queued", "text": msg["text"]})
            elif msg.get("type") == "career_start":
                from career_workflow import run_career_search, is_running as career_running
                from telegram_bot import send_message
                if not career_running():
                    asyncio.create_task(
                        run_career_search(msg.get("keywords", ""), broadcast, send_message),
                        name="career_workflow",
                    )
                else:
                    await broadcast({"type": "career_activity", "text": "Career search already in progress."})
            elif msg.get("type") == "approve":
                import tools.approval as ag
                ag.resolve(msg["id"], True)
                await broadcast({"type": "approved", "id": msg["id"]})
            elif msg.get("type") == "deny":
                import tools.approval as ag
                ag.resolve(msg["id"], False)
                await broadcast({"type": "denied", "id": msg["id"]})
    except WebSocketDisconnect:
        _ws_clients.remove(ws)


@api.post("/api/speak")
async def api_speak(request: Request):
    body = await request.json()
    text = str(body.get("text", ""))[:600].strip()
    if not text:
        return {"ok": False, "reason": "empty text"}
    voice = body.get("voice", "Reed (English (UK))")
    try:
        asyncio.create_task(
            asyncio.create_subprocess_exec("say", "-v", voice, text,
                                           stdout=asyncio.subprocess.DEVNULL,
                                           stderr=asyncio.subprocess.DEVNULL)
        )
        return {"ok": True}
    except (FileNotFoundError, OSError):
        # `say` is macOS-only; on Windows/Linux this is a silent no-op
        return {"ok": False, "reason": "say not available on this OS"}


@api.get("/api/voices")
async def api_voices():
    try:
        result = subprocess.run(["say", "-v", "?"], capture_output=True, text=True)
    except (FileNotFoundError, OSError):
        return {"voices": [], "reason": "say not available on this OS"}
    voices = []
    for line in result.stdout.strip().split("\n"):
        parts = line.split()
        for i, part in enumerate(parts):
            if len(part) == 5 and "_" in part:
                name = " ".join(parts[:i])
                if part.startswith("en") and name:
                    voices.append(name)
                break
    return {"voices": voices}


@api.get("/api/status")
async def api_status():
    from orchestrator import is_running
    from telegram_bot import task_queue
    import tools.approval as ag
    return {
        "running": is_running(),
        "queued": task_queue.qsize(),
        "pending_approvals": ag.pending_ids(),
        "session_tokens": _session_tokens,
        "weekly_tokens": _weekly_tokens,
    }


# ── Usage tracking ───────────────────────────────────────────────────────────

USAGE_FILE = Path(__file__).parent / "usage.json"

_session_tokens: int = 0   # tokens used since this server process started
_weekly_tokens: int = 0    # tokens used this calendar week (persisted)


def _current_week() -> str:
    return datetime.date.today().strftime("%Y-W%W")


def _load_weekly():
    global _weekly_tokens
    if USAGE_FILE.exists():
        try:
            data = json.loads(USAGE_FILE.read_text())
            if data.get("week") == _current_week():
                _weekly_tokens = data.get("tokens", 0)
        except Exception:
            pass


def _save_weekly():
    try:
        USAGE_FILE.write_text(json.dumps({"week": _current_week(), "tokens": _weekly_tokens}))
    except Exception as e:
        log.warning("Failed to save usage: %s", e)


# ── Orchestrator worker ──────────────────────────────────────────────────────

_session_id: str | None = None


def _escape_md(text: str) -> str:
    """Escape Telegram Markdown V1 special characters."""
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


async def _tg_safe(send_telegram, text: str):
    """Send to Telegram, suppressing errors so the bot never crashes on send failures."""
    try:
        await send_telegram(text)
    except Exception as e:
        log.warning("Telegram send failed: %s", e)


async def orchestrator_worker(task_queue, send_telegram):
    global _session_id, _session_tokens, _weekly_tokens
    from orchestrator import run_task

    while True:
        task = await task_queue.get()
        log.info("Starting task: %s", task)

        await broadcast({"type": "task_started", "text": task})

        try:
            await _tg_safe(send_telegram, f"Starting: _{_escape_md(task)}_")
            result, _session_id, usage = await run_task(
                task,
                send_telegram=send_telegram,
                session_id=_session_id,
                broadcast=broadcast,
            )
            tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            _session_tokens += tokens
            _weekly_tokens += tokens
            _save_weekly()
            await broadcast({"type": "usage_update", "session_tokens": _session_tokens, "weekly_tokens": _weekly_tokens})
            short = result[:3000] if result else "(done)"
            await broadcast({"type": "task_done", "text": short})
            await _tg_safe(send_telegram, f"Done:\n\n{short}")
        except Exception as e:
            log.exception("Task failed: %s", task)
            err = str(e)
            await broadcast({"type": "task_error", "text": err})
            await _tg_safe(send_telegram, f"Task failed: {err}")
        finally:
            task_queue.task_done()


# ── Main ─────────────────────────────────────────────────────────────────────

async def main():
    from telegram_bot import build_app, task_queue, send_message
    import tools.approval as ag
    _load_weekly()

    tg_app = build_app()

    async def approval_sender(action_id: str, text: str):
        from telegram_bot import send_approval_request
        await send_approval_request(action_id, text)
        await broadcast({"type": "approval_request", "id": action_id, "text": text})

    ag.register_sender(approval_sender)

    uvicorn_config = uvicorn.Config(
        api,
        host="127.0.0.1",
        port=DASHBOARD_PORT,
        log_level="warning",
    )
    uvicorn_server = uvicorn.Server(uvicorn_config)

    # Initialize and start telegram app manually so we control shutdown order
    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling(drop_pending_updates=True)
    log.info("Telegram bot started.")
    log.info("Dashboard: http://localhost:%d", DASHBOARD_PORT)

    tasks = [
        asyncio.create_task(uvicorn_server.serve(), name="uvicorn"),
        asyncio.create_task(orchestrator_worker(task_queue, send_message), name="worker"),
        asyncio.create_task(ag.watch_approvals(), name="approval_watcher"),
    ]

    try:
        await asyncio.gather(*tasks)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except Exception as e:
        log.exception("Fatal error: %s", e)
    finally:
        for t in tasks:
            t.cancel()
        uvicorn_server.should_exit = True
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()
        log.info("Shut down cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutting down.")
