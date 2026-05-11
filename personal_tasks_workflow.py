"""
Personal Tasks workflow:
  1. Handle personal tasks like homework, studying, or general organization.
  2. Research or process the specific task.
  3. Generate a plan or summary and log to the Vault.
"""

import asyncio
import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

_running = False
_tasks: list[dict] = []

def is_running() -> bool:
    return _running

async def _claude(prompt: str, broadcast) -> str:
    from tools.llm import run_llm_command
    res = await run_llm_command(
        prompt=prompt,
        broadcast=broadcast,
        allowed_tools="Bash,Read"
    )
    return res["result"]

async def run_personal_task(task_description: str, broadcast, send_telegram):
    """
    Handles personal task organization and planning.
    
    Stages:
      1. Analyze: Categorizes the task and creates a plan via LLM.
      2. Execute/Research: Placeholder for task-specific actions.
      3. Report: Writes the task plan to the Obsidian Vault and notifies via Telegram.
    """
    global _running, _tasks
    _running = True

    try:
        # ── Stage 1: Analyze ──────────────────────────────────────────
        await broadcast({"type": "personal_stage", "stage": "analyze"})
        await broadcast({"type": "personal_activity", "text": f"Analyzing personal task: {task_description}"})

        analysis = await _claude(f"""
Analyze this personal task and create a structured plan.
Task: {task_description}

Consider:
- Goal of the task
- Priority and estimated effort
- Step-by-step breakdown
- Resources needed (if any)

Output ONLY JSON:
{{
    "task": "{task_description}",
    "plan": ["step 1", "step 2"],
    "priority": "high/medium/low",
    "estimated_time": "e.g. 2 hours",
    "category": "Homework/Study/Admin/Other"
}}
""", broadcast)

        from tools.utils import extract_json
        data = extract_json(analysis)
        
        if isinstance(data, list) and len(data) > 0:
            data = data[0]

        if not isinstance(data, dict):
            data = {
                "task": task_description,
                "plan": ["Research further", "Complete task"],
                "priority": "medium",
                "estimated_time": "unknown",
                "category": "Other"
            }

        # ── Stage 2: Execute/Research ─────────────────────────────────
        await broadcast({"type": "personal_stage", "stage": "execute"})
        await broadcast({"type": "personal_activity", "text": f"Developing plan for {data.get('category')}..."})
        
        # ── Stage 3: Report ───────────────────────────────────────────
        await broadcast({"type": "personal_stage", "stage": "report"})
        
        # Log to Jarvis Hub
        try:
            from tools.logger import log_completed_task
            log_completed_task(
                task_name=data['task'],
                description=f"Personal task categorized as {data['category']} with {data['priority']} priority.",
                actions=[f"Plan: {', '.join(data.get('plan', []))}", f"Est. time: {data.get('estimated_time', 'unknown')}"]
            )
            await broadcast({"type": "personal_activity", "text": "Task plan logged to Jarvis Hub."})
        except Exception as le:
            log.warning(f"Failed to log personal task to Hub: {le}")

        await send_telegram(f"✅ *Personal Task Logged*\nTask: {data['task']}\nCategory: {data['category']}\nPlan saved to Vault.")
        await broadcast({"type": "personal_activity", "text": "Workflow complete."})

    except Exception as e:
        log.exception("Personal task workflow error")
        await broadcast({"type": "personal_error", "text": str(e)})
    finally:
        _running = False
