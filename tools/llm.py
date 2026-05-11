"""
Unified LLM runner for Agentic OS.
Tries Claude first (via Claude Code CLI), falls back to Gemini (via Gemini CLI).
"""

import asyncio
import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

AGENTIC_DIR = Path(__file__).parent.parent
_active_proc: asyncio.subprocess.Process | None = None
_preferred_provider: str = "auto"  # "auto", "claude", or "gemini"

def get_active_proc():
    return _active_proc

def stop_active_proc():
    global _active_proc
    if _active_proc:
        try:
            _active_proc.terminate()
            log.info("Process terminated by user.")
            return True
        except Exception as e:
            log.warning(f"Failed to terminate process: {e}")
    return False

def set_preferred_provider(provider: str):
    global _preferred_provider
    if provider in ["auto", "claude", "gemini"]:
        _preferred_provider = provider
        log.info(f"PROVIDER_SWITCH: Preferred provider now {provider}")

def get_preferred_provider():
    return _preferred_provider

async def run_llm_command(
    prompt: str,
    broadcast=None,
    session_id: str | None = None,
    allowed_tools: str = "Bash,Read,Write,Edit,WebSearch,WebFetch",
    send_telegram=None
):
    """
    Tries to run the prompt with Claude. If it fails or Claude is unavailable,
    falls back to Gemini. Respects _preferred_provider.
    """
    global _preferred_provider
    
    # 1. Manual override
    if _preferred_provider == "claude":
        return await _run_claude(prompt, broadcast, session_id, allowed_tools, send_telegram)
    elif _preferred_provider == "gemini":
        return await _run_gemini_retry(prompt, broadcast, session_id, send_telegram)

    # 2. Auto Fallback logic
    log.info(f"AUTO: Attempting Claude")
    result = await _run_claude(prompt, broadcast, session_id, allowed_tools, send_telegram)
    
    out_text = result["result"].lower()
    usage_keywords = ["out of usage", "limit reached", "out of extra usage", "quota exceeded", "usage limit"]
    usage_error = any(kw in out_text for kw in usage_keywords)
    
    # Check if Claude returned an actual message or just an error
    has_content = result["usage"].get("output_tokens", 0) > 0 or len(result["result"].strip()) > 100
    
    if not result["success"] or usage_error or not has_content:
        if usage_error:
            log.warning("Claude reported usage limit.")
            if broadcast:
                await broadcast({"type": "assistant", "message": {"content": [{"type": "text", "text": "\n[System: Claude Limit detected. Switching to Gemini...]\n"}]}})
        else:
            log.warning(f"Claude failed or returned empty. Switching to Gemini...")
            
        return await _run_gemini_retry(prompt, broadcast, session_id, send_telegram)

    return result

async def _run_gemini_retry(prompt, broadcast, session_id, send_telegram):
    res = await _run_gemini(prompt, broadcast, session_id, send_telegram)
    if "exhausted your capacity" in res["result"].lower():
        log.warning("Gemini Quota hit. Waiting 10s and retrying...")
        if broadcast:
            await broadcast({"type": "assistant", "message": {"content": [{"type": "text", "text": "\n[System: Gemini Quota hit. Retrying in 10s...]\n"}]}})
        await asyncio.sleep(10)
        return await _run_gemini(prompt, broadcast, session_id, send_telegram)
    return res

async def _run_claude(prompt, broadcast, session_id, allowed_tools, send_telegram):
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--allowedTools", allowed_tools, "--dangerously-skip-permissions"]
    if session_id: cmd += ["--resume", session_id]
    return await _run_cli_process(cmd, broadcast, send_telegram)

async def _run_gemini(prompt, broadcast, session_id, send_telegram):
    # Using 'gemini-3-pro-preview' as the smartest available model
    cmd = ["gemini", "-p", prompt, "-m", "gemini-3-pro-preview", 
           "--output-format", "stream-json", "--yolo", "--skip-trust"]
    if session_id: cmd += ["--resume", session_id]
    res = await _run_cli_process(cmd, broadcast, send_telegram)
    res["provider"] = "gemini"
    return res

def _extract_json_objects(text):
    """Extracts all valid JSON objects from a string that might contain mixed text."""
    objs = []
    # Simple regex to find potential JSON objects
    for match in re.finditer(r'\{[^{}]*\}', text):
        try:
            objs.append(json.loads(match.group(0)))
        except:
            pass
    if not objs:
        stack = 0
        start = -1
        for i, char in enumerate(text):
            if char == '{':
                if stack == 0: start = i
                stack += 1
            elif char == '}':
                stack -= 1
                if stack == 0 and start != -1:
                    try:
                        objs.append(json.loads(text[start:i+1]))
                    except:
                        pass
    return objs

async def _run_cli_process(cmd, broadcast, send_telegram):
    global _active_proc
    log.info(f"EXEC: {' '.join(cmd[:3])} ...")
    
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(AGENTIC_DIR),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        limit=10 * 1024 * 1024
    )
    _active_proc = proc

    assistant_text = ""
    garbage_text = ""
    new_session_id = ""
    usage = {"input_tokens": 0, "output_tokens": 0}
    is_gemini = "gemini" in cmd[0]

    async def read_stream(stream, is_stderr=False):
        nonlocal assistant_text, garbage_text, new_session_id
        async for raw_line in stream:
            try:
                line = raw_line.decode().strip()
            except: continue
            if not line: continue
            
            objs = _extract_json_objects(line)
            if not objs:
                garbage_text += line + " "
                continue
                
            for event in objs:
                if broadcast: await broadcast(event)
                
                etype = event.get("type")
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
                    usage["input_tokens"] = u.get("input_tokens", 0)
                    usage["output_tokens"] = u.get("output_tokens", 0)
                elif etype == "message" and is_gemini and event.get("role") == "assistant":
                    if event.get("content"):
                        assistant_text += event["content"]

    await asyncio.gather(read_stream(proc.stdout), read_stream(proc.stderr, is_stderr=True))
    await proc.wait()
    _active_proc = None
    
    res_text = assistant_text.strip()
    if not res_text:
        res_text = garbage_text.strip()

    return {
        "success": proc.returncode == 0,
        "exit_code": proc.returncode,
        "result": res_text,
        "session_id": new_session_id,
        "usage": usage,
        "provider": "claude" if not is_gemini else "gemini"
    }
