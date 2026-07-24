"""
LLM runner for PAIS.
Runs tasks via the Claude Code CLI (claude -p) using Taran's Claude subscription.
If Claude hits a usage limit, waits 30 s and retries once before failing gracefully.

Also translates every tool call into plain-English `agent_work` events so the
Work tab can show what each agent is doing in human-readable terms.
"""

import asyncio
import contextvars
import datetime
import json
import logging
import os
import re
import signal
from pathlib import Path

log = logging.getLogger(__name__)

AGENTIC_DIR = Path(__file__).parent.parent

# Hard wall-clock cap on a single CLI run. A hung child (stuck pytest, wedged
# MCP/tool subprocess, network stall) is killed — the whole process GROUP —
# instead of wedging the worker forever. Generous so legit long research/coding
# runs finish; override with PAIS_CLI_TIMEOUT (seconds).
CLI_TIMEOUT_SECS = int(os.environ.get("PAIS_CLI_TIMEOUT", "1800"))

# Per-task process tracking — keyed by task_id so /stop can target one task
_active_procs: dict = {}

# Per-agent model routing. Anything not listed falls back to DEFAULT_MODEL.
# vault_curator runs ~half of all calls — Haiku is plenty for cross-linking + indexing.
# briefing is the most-read output and has the multi-source price rule — worth Opus.
# coding runs Plan→Lint→Edit→Test→Verify — quality dominates the few calls it makes.
DEFAULT_MODEL = "sonnet"
MODEL_BY_AGENT = {
    "vault_curator": "haiku",
    "briefing":      "opus",
    "coding":        "opus",
    "risk_gate":     "opus",
    "classify":      "haiku",   # cheap first-pass signal gate (executor)
}

def _model_for(agent_name: str | None) -> str:
    return MODEL_BY_AGENT.get(agent_name or "", DEFAULT_MODEL)


# Usage callback — main.py registers a function that accumulates tokens
_usage_callback = None

# ContextVar: lets task_id propagate through async workflows without changing
# every function signature. Main.py sets this before invoking a workflow.
_current_task_id: contextvars.ContextVar = contextvars.ContextVar("task_id", default=None)


def set_current_task_id(tid: str | None):
    _current_task_id.set(tid)


def register_usage_callback(fn):
    """Main.py registers a callback that receives (agent_name, usage_dict)."""
    global _usage_callback
    _usage_callback = fn


def get_active_proc():
    """Return one active proc (legacy compat — used by is_running checks)."""
    return next(iter(_active_procs.values()), None) if _active_procs else None


def _kill_proc_group(proc, sig=signal.SIGTERM):
    """Signal the whole process GROUP. The `claude` CLI spawns node/MCP/tool
    children; SIGTERM to the parent alone orphans them. Falls back to killing
    just the process if the group send fails (e.g. it already exited)."""
    try:
        os.killpg(os.getpgid(proc.pid), sig)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except Exception:
            pass


def stop_active_proc(task_id: str | None = None):
    """Terminate one task's process GROUP (by task_id) or all if no id given."""
    if not _active_procs:
        return False
    targets = []
    if task_id and task_id in _active_procs:
        targets = [(task_id, _active_procs[task_id])]
    elif task_id is None:
        targets = list(_active_procs.items())
    if not targets:
        return False
    for tid, proc in targets:
        try:
            _kill_proc_group(proc, signal.SIGTERM)
        except Exception as e:
            log.warning("Failed to terminate proc group for %s: %s", tid, e)
    log.info("Terminated %d active process group(s).", len(targets))
    return True


def get_preferred_provider() -> str:
    return "claude"


def set_preferred_provider(_provider: str):
    """Deprecated — Claude is the only provider. Kept as no-op for legacy callers."""
    return


# ── Plain-English tool translator ─────────────────────────────────────────────

def _translate_tool(tool_name: str, tool_input: dict) -> tuple[str, str, str, str]:
    """
    Returns (action_type, icon, label, detail).
    Used to emit human-readable agent_work events from raw Claude tool calls.
    """
    if tool_name == "Read":
        path = tool_input.get("file_path", "")
        fname = Path(path).name if path else "file"
        return "read", "📖", f"Reading {fname}", _shorten_path(path)

    if tool_name == "Write":
        path = tool_input.get("file_path", "")
        fname = Path(path).name if path else "file"
        return "write", "✏️", f"Writing {fname}", _shorten_path(path)

    if tool_name == "Edit":
        path = tool_input.get("file_path", "")
        fname = Path(path).name if path else "file"
        return "edit", "✏️", f"Editing {fname}", _shorten_path(path)

    if tool_name == "WebSearch":
        q = (tool_input.get("query", "") or "")[:60]
        return "web", "🌐", f"Searching web: {q}", ""

    if tool_name == "WebFetch":
        url = (tool_input.get("url", "") or "")
        domain = re.sub(r'^https?://(www\.)?', '', url).split('/')[0][:40]
        return "web", "🌐", f"Fetching {domain}", url[:80]

    if tool_name == "Bash":
        cmd   = (tool_input.get("command", "") or "").strip()
        desc  = (tool_input.get("description", "") or "").strip()

        # If the caller provided a description, use it (it's already human-readable)
        if desc:
            return "bash", "💻", desc[:90], ""

        # Parse common shell patterns
        if "dispatch.sh" in cmd or "dispatch_agent.sh" in cmd:
            m = re.search(r'dispatch(?:_agent)?\.sh\s+(\w+)', cmd)
            agent = m.group(1) if m else "agent"
            return "dispatch", "🚀", f"Spawning {agent} agent", ""

        if re.search(r'\bcat\b.*\.md', cmd):
            m = re.search(r'cat "?([^"\s|;]+\.md)"?', cmd)
            fname = Path(m.group(1)).name if m else "file"
            return "read", "📖", f"Reading {fname}", m.group(1)[:60] if m else ""

        if re.search(r'\bcat\b', cmd) and "<<" not in cmd:
            return "read", "📖", "Reading file", ""

        if re.search(r'cat\s*[>|]|cat\s*>>', cmd) or ("cat >" in cmd):
            return "write", "✏️", "Writing file to vault", ""

        if "printf" in cmd and ">>" in cmd:
            m = re.search(r'>>\s*"?([^"\s;]+)"?', cmd)
            fname = Path(m.group(1)).name if m else "file"
            return "write", "✏️", f"Appending to {fname}", ""

        if re.search(r'\bfind\b', cmd):
            target = "vault" if "Brain" in cmd or "obsidian" in cmd else "files"
            return "scan", "🔍", f"Scanning {target} for matching files", ""

        if re.search(r'\bgrep\b', cmd):
            m = re.search(r'grep\s+(?:-\w+\s+)*"?([^"\s]+)"?', cmd)
            pattern = m.group(1)[:40] if m else "pattern"
            return "search", "🔍", f"Searching for \"{pattern}\"", ""

        if re.search(r'\bpython3\b', cmd):
            m = re.search(r'python3\s+([^\s]+)', cmd)
            script = Path(m.group(1)).name if m else "script"
            return "run", "⚙️", f"Running {script}", ""

        if re.search(r'\bffmpeg\b', cmd):
            return "run", "🎬", "Assembling video with ffmpeg", ""

        if re.search(r'\bcurl\b', cmd):
            m = re.search(r'https?://([^/\s"\']+)', cmd)
            domain = m.group(1)[:40] if m else "URL"
            return "fetch", "🌐", f"Fetching {domain}", ""

        if re.search(r'\bmkdir\b', cmd):
            return "fs", "📁", "Creating directory", ""

        if re.search(r'\brm\b', cmd):
            return "fs", "🗑️", "Removing file", ""

        if re.search(r'\bls\b|\bstat\b|\bwc\b', cmd):
            return "read", "📖", "Checking files", ""

        if re.search(r'\btail\b|\bhead\b', cmd):
            return "read", "📖", "Reading recent lines", ""

        if re.search(r'\bsed\b|\bawk\b', cmd):
            return "edit", "✏️", "Processing text", ""

        # Fallback: show trimmed command
        short = cmd[:75].replace("\n", " ").strip()
        if len(cmd) > 75:
            short += "…"
        return "bash", "💻", short, ""

    # Unknown tool — show name
    return "other", "🔧", f"Using tool: {tool_name}", ""


def _shorten_path(path: str) -> str:
    """Shorten vault paths to just the relevant part."""
    if not path:
        return ""
    path = str(path)
    # Strip home dir prefix
    home = str(Path.home())
    if path.startswith(home):
        path = "~" + path[len(home):]
    # Strip vault prefix
    vault_marker = "Digital Brain/"
    idx = path.find(vault_marker)
    if idx != -1:
        path = path[idx + len(vault_marker):]
    return path[:60]


# ── Main entry point ──────────────────────────────────────────────────────────

async def run_llm_command(
    prompt: str,
    broadcast=None,
    session_id: str | None = None,
    allowed_tools: str = "Bash,Read,Write,Edit,WebSearch,WebFetch",
    send_telegram=None,
    sandbox_dir=None,
    agent_name: str = "",
    task_id: str | None = None,
) -> dict:
    """
    Run a prompt with Claude. If a usage limit is detected, waits 30 s and
    retries once. Returns the result dict on success or the error result on
    second failure.

    When PAIS_LLM_BACKEND=api (machines with no `claude` CLI, e.g. Oracle), the
    call is served by the Anthropic Messages API instead of the CLI subprocess.
    """
    if os.environ.get("PAIS_LLM_BACKEND") == "api":
        from tools.llm_api import run_llm_api
        return await run_llm_api(prompt, agent_name=agent_name)

    result = await _run_claude(prompt, broadcast, session_id, allowed_tools,
                               send_telegram, sandbox_dir, agent_name, task_id)

    usage_keywords = [
        "out of usage", "limit reached", "out of extra usage",
        "quota exceeded", "usage limit",
        "529", "overloaded",
    ]
    out_lower = result["result"].lower()
    usage_hit = any(kw in out_lower for kw in usage_keywords)
    empty = not result["success"] and not result["result"].strip()

    if usage_hit or empty:
        msg = "\n[PAIS: Claude usage limit hit — retrying in 30 s…]\n"
        log.warning("Claude usage limit or empty response — retrying in 30 s.")
        if broadcast:
            await broadcast({"type": "assistant",
                             "message": {"content": [{"type": "text", "text": msg}]}})
        await asyncio.sleep(30)
        result = await _run_claude(prompt, broadcast, session_id, allowed_tools,
                                   send_telegram, sandbox_dir, agent_name, task_id)

    # Notify main.py to accumulate tokens for every agent (not just general)
    if _usage_callback and result.get("usage"):
        try:
            usage_with_model = {**result["usage"], "model": _model_for(agent_name)}
            _usage_callback(agent_name or "general", usage_with_model)
        except Exception as e:
            log.warning("usage_callback error: %s", e)

    return result


async def _run_claude(prompt, broadcast, session_id, allowed_tools, send_telegram,
                      sandbox_dir=None, agent_name="", task_id=None):
    cmd = [
        "claude", "-p", prompt,
        "--model", _model_for(agent_name),
        "--output-format", "stream-json", "--verbose",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--allowedTools", allowed_tools, "--dangerously-skip-permissions",
    ]
    if session_id:
        cmd += ["--resume", session_id]
    return await _run_cli_process(cmd, broadcast, send_telegram, sandbox_dir, agent_name, task_id)


def _extract_json_objects(text: str) -> list:
    """Extract outermost JSON objects from a string that may contain mixed text."""
    objs = []
    stack = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if stack == 0:
                start = i
            stack += 1
        elif ch == "}":
            stack -= 1
            if stack == 0 and start != -1:
                try:
                    objs.append(json.loads(text[start : i + 1]))
                except Exception:
                    pass
    if not objs:
        for match in re.finditer(r"\{[^{}]*\}", text):
            try:
                objs.append(json.loads(match.group(0)))
            except Exception:
                pass
    return objs


async def _run_cli_process(cmd, broadcast, send_telegram, sandbox_dir=None,
                           agent_name="", task_id=None) -> dict:
    log.info("EXEC: %s …", " ".join(cmd[:3]))

    work_dir = str(sandbox_dir) if sandbox_dir else str(AGENTIC_DIR)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=work_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=10 * 1024 * 1024,
        start_new_session=True,   # own process group → killpg reaps the whole tree
    )
    # Track by task_id so /stop can target one task; fall back to contextvar then a generated key
    proc_key = task_id or _current_task_id.get() or f"_anon_{id(proc)}"
    _active_procs[proc_key] = proc

    assistant_text = ""
    garbage_text   = ""
    new_session_id = ""
    usage = {"input_tokens": 0, "output_tokens": 0,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
             "cost_usd": 0.0}

    async def read_stream(stream):
        nonlocal assistant_text, garbage_text, new_session_id
        while True:
            try:
                raw_line = await stream.readline()
            except (ValueError, asyncio.LimitOverrunError):
                # A single line exceeded the 10 MB buffer — don't let the
                # uncaught error abort the reader and leak the subprocess.
                log.warning("CLI stream line exceeded buffer limit — truncating this stream")
                break
            if not raw_line:
                break  # EOF
            try:
                line = raw_line.decode().strip()
            except Exception:
                continue
            if not line:
                continue

            objs = _extract_json_objects(line)
            if not objs:
                garbage_text += line + " "
                continue

            for event in objs:
                if broadcast:
                    # Inject agent_name so handleEvent can route to the correct agent page feed
                    ev_out = {**event, "agent": agent_name} if agent_name and "agent" not in event else event
                    await broadcast(ev_out)

                etype = event.get("type")

                # ── Translate tool calls to plain English ──────────────────
                if etype == "assistant" and broadcast and agent_name:
                    for block in event.get("message", {}).get("content", []):
                        if block.get("type") == "tool_use":
                            t_name  = block.get("name", "")
                            t_input = block.get("input", {}) or {}
                            action, icon, label, detail = _translate_tool(t_name, t_input)
                            await broadcast({
                                "type":    "agent_work",
                                "agent":   agent_name,
                                "task_id": task_id or _current_task_id.get(),
                                "action":  action,
                                "icon":    icon,
                                "label":   label,
                                "detail":  detail,
                                "ts":      datetime.datetime.now().isoformat(),
                            })

                if etype == "system":
                    new_session_id = event.get("session_id") or new_session_id
                elif etype == "assistant":
                    for block in event.get("message", {}).get("content", []):
                        if block.get("type") == "text":
                            assistant_text += block["text"]
                elif etype == "result":
                    if event.get("result"):
                        assistant_text = event["result"]
                    new_session_id = event.get("session_id") or new_session_id
                    u = event.get("usage") or {}
                    usage["input_tokens"]                = u.get("input_tokens", 0)
                    usage["output_tokens"]               = u.get("output_tokens", 0)
                    usage["cache_creation_input_tokens"] = u.get("cache_creation_input_tokens", 0)
                    usage["cache_read_input_tokens"]     = u.get("cache_read_input_tokens", 0)
                    usage["cost_usd"]                    = event.get("total_cost_usd", 0.0)

    async def _pump():
        await asyncio.gather(read_stream(proc.stdout), read_stream(proc.stderr))
        await proc.wait()

    timed_out = False
    try:
        await asyncio.wait_for(_pump(), timeout=CLI_TIMEOUT_SECS)
    except asyncio.TimeoutError:
        timed_out = True
        log.warning("CLI run %s exceeded %ds — killing process group", proc_key, CLI_TIMEOUT_SECS)
        _kill_proc_group(proc, signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except asyncio.TimeoutError:
            _kill_proc_group(proc, signal.SIGKILL)
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                log.error("CLI run %s did not exit even after SIGKILL", proc_key)
    finally:
        # Always drop the handle so /stop and is_running() can't see a dead proc.
        _active_procs.pop(proc_key, None)

    res_text = assistant_text.strip() or garbage_text.strip()
    if timed_out and not res_text:
        res_text = f"[PAIS: agent run exceeded {CLI_TIMEOUT_SECS}s and was terminated]"

    return {
        "success":    (proc.returncode == 0) and not timed_out,
        "exit_code":  proc.returncode,
        "result":     res_text,
        "session_id": new_session_id,
        "usage":      usage,
        "provider":   "claude",
    }
