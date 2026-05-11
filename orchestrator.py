"""
Subprocess orchestrator — runs tasks via `claude -p` (Claude Code CLI).
Claude handles all tool execution internally (Bash, file I/O, web search).
No Anthropic API key needed — uses your Claude Code subscription.
"""

import asyncio
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

AGENTIC_DIR = Path(__file__).parent
def is_running() -> bool:
    from tools.llm import get_active_proc
    proc = get_active_proc()
    return proc is not None and proc.returncode is None


async def run_task(
    task: str,
    send_telegram,
    session_id: str | None = None,
    broadcast=None,
) -> tuple[str, str, dict]:
    """
    Run a task using the unified LLM runner (Claude with Gemini fallback).
    Returns (result_text, new_session_id, usage_dict).
    """
    from tools.llm import run_llm_command
    
    res = await run_llm_command(
        prompt=task,
        broadcast=broadcast,
        session_id=session_id,
        send_telegram=send_telegram
    )
    
    return res["result"], res["session_id"], res["usage"]
