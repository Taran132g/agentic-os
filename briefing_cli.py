#!/usr/bin/env python3
"""Headless entry point for the briefing workflow — built for n8n (2026-05-29).

n8n (or cron, or any scheduler) runs:

    python3 ~/agentic_os/briefing_cli.py "daily briefing"

It runs the SAME `run_briefing_task` the dashboard uses, but with a no-op
broadcast (no websocket clients) and the real Telegram sender, so the briefing
lands in Telegram + vault exactly as if triggered from the UI. The LLM summary
step inside the workflow uses the subscription `claude` CLI (no API tokens).

Exit code 0 = success, 1 = failure (so n8n's error branch can fire).
"""

import asyncio
import sys
import uuid
from pathlib import Path

AGENTIC_DIR = Path(__file__).parent


async def _noop_broadcast(_msg: dict):
    """No websocket clients in a headless run — swallow dashboard events."""
    return None


async def _main(task_text: str) -> str:
    from briefing_workflow import run_briefing_task
    from telegram_bot import send_message as tg
    from tools.llm import set_current_task_id

    tid = f"briefing_cli_{uuid.uuid4().hex[:8]}"
    set_current_task_id(tid)

    sandbox = AGENTIC_DIR / "tmp" / tid
    sandbox.mkdir(parents=True, exist_ok=True)

    result = await run_briefing_task(task_text, _noop_broadcast, tg, sandbox_dir=sandbox)
    return result or "Briefing complete."


if __name__ == "__main__":
    task = sys.argv[1] if len(sys.argv) > 1 else "daily briefing"
    try:
        out = asyncio.run(_main(task))
        print(out)
        sys.exit(0)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"BRIEFING FAILED: {e}", file=sys.stderr)
        sys.exit(1)
