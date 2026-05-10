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
_active_proc: asyncio.subprocess.Process | None = None


def is_running() -> bool:
    return _active_proc is not None and _active_proc.returncode is None


async def run_task(
    task: str,
    send_telegram,
    session_id: str | None = None,
    broadcast=None,
) -> tuple[str, str]:
    """
    Run a task using the Claude Code CLI.
    Returns (result_text, new_session_id, usage_dict).
    broadcast(event) is called for each streaming event — used by the dashboard.
    """
    global _active_proc

    cmd = [
        "claude",
        "-p", task,
        "--output-format", "stream-json",
        "--verbose",
        "--strict-mcp-config",
        "--mcp-config", '{"mcpServers":{}}',
        "--allowedTools", "Bash,Read,Write,Edit,WebSearch,WebFetch",
        "--dangerously-skip-permissions",
    ]
    if session_id:
        cmd += ["--resume", session_id]

    log.info("Launching: %s", " ".join(cmd[:4]) + " ...")

    _active_proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(AGENTIC_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=10 * 1024 * 1024,  # 10 MB — prevents LimitOverrunError on large responses
    )

    result_text = "(no response)"
    new_session_id = session_id or ""
    task_usage: dict = {"input_tokens": 0, "output_tokens": 0}

    async for raw_line in _active_proc.stdout:
        line = raw_line.decode().strip()
        if not line:
            continue

        try:
            event = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            log.debug("Non-JSON stdout: %s", line[:120])
            continue

        if broadcast:
            await broadcast(event)

        etype = event.get("type", "")

        if etype == "system":
            sid = event.get("session_id")
            if sid:
                new_session_id = sid

        elif etype == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    text = block["text"].strip()
                    if text and len(text) > 60:
                        try:
                            await send_telegram(text[:600])
                        except Exception as e:
                            log.warning("Telegram send error: %s", e)

        elif etype == "result":
            result_text = event.get("result") or result_text
            new_session_id = event.get("session_id") or new_session_id
            u = event.get("usage") or {}
            task_usage["input_tokens"] = u.get("input_tokens", 0)
            task_usage["output_tokens"] = u.get("output_tokens", 0)

    stderr_output = (await _active_proc.stderr.read()).decode().strip()
    if stderr_output:
        log.warning("stderr: %s", stderr_output[:500])

    await _active_proc.wait()
    _active_proc = None
    return result_text, new_session_id, task_usage
