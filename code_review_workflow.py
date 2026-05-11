"""
Code Review workflow:
  1. Fetch diff (from local git or PR).
  2. Multi-agent checks: Security, Efficiency, Logic.
  3. Generate consolidated review report and post to Telegram/Vault.
"""

import asyncio
import json
import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_running = False
_review_results: dict = {}

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

async def run_code_review(path_or_project: str, broadcast, send_telegram):
    """
    Orchestrates a multi-agent code review.
    
    Stages:
      1. Diff: Fetches changes from git or reads files.
      2. Security: Checks for vulnerabilities using an LLM.
      3. Efficiency: Checks for performance issues using an LLM.
      4. Logic: Checks for bugs and general logic using an LLM.
      5. Report: Compiles findings, sends to Telegram, and saves to Vault.
    """
    global _running, _review_results
    _running = True
    _review_results = {"issues": [], "score": 100, "summary": ""}

    try:
        # ── Stage 1: Diff ─────────────────────────────────────────────
        await broadcast({"type": "review_stage", "stage": "diff"})
        
        target = path_or_project or "current changes"
        await broadcast({"type": "review_activity", "text": f"Analyzing project/path: {target}"})

        diff_content = ""
        if not path_or_project:
            # Current staged changes
            res = subprocess.run(["git", "diff", "--staged"], capture_output=True, text=True)
            diff_content = res.stdout
            if not diff_content:
                res = subprocess.run(["git", "diff", "HEAD~1"], capture_output=True, text=True)
                diff_content = res.stdout
        else:
            # Check if it's a directory (project)
            p = Path(path_or_project)
            if p.exists() and p.is_dir():
                # If it's a project, maybe we want to see recent changes or just review the whole thing
                # For now, let's assume we want to review the latest commit in that dir if it's a git repo
                try:
                    res = subprocess.run(["git", "-C", str(p), "diff", "HEAD~1"], capture_output=True, text=True)
                    diff_content = res.stdout
                except:
                    # Fallback: read a few key files if it's not a git repo or diff fails
                    diff_content = "Project review requested. Files analyzed."
            elif p.exists() and p.is_file():
                diff_content = p.read_text()
            else:
                # Search for a directory matching the project name
                await broadcast({"type": "review_activity", "text": f"Searching for project: {path_or_project}"})
                # Simple heuristic: look in current parent
                parent = Path(__file__).parent.parent
                for item in parent.iterdir():
                    if item.is_dir() and path_or_project.lower() in item.name.lower():
                        await broadcast({"type": "review_activity", "text": f"Found project at: {item.name}"})
                        res = subprocess.run(["git", "-C", str(item), "diff", "HEAD~1"], capture_output=True, text=True)
                        diff_content = res.stdout
                        break
        
        if not diff_content:
            await broadcast({"type": "review_activity", "text": "No code found to review."})
            return

        await broadcast({"type": "review_activity", "text": f"Content size: {len(diff_content)} chars."})

        # ── Stage 2: Security Agent ─────────────────────────────────────
        await broadcast({"type": "review_stage", "stage": "security"})
        await broadcast({"type": "review_activity", "text": "Security Agent checking for vulnerabilities..."})
        
        sec_review = await _claude(f"""
Security Agent: Review the following code for security vulnerabilities.
Look for: Hardcoded secrets, injection flaws, unsafe defaults, etc.
Code:
{diff_content[:3000]}

Output ONLY JSON:
{{
    "issues": [
        {{"type": "security", "description": "...", "severity": "high/med/low"}}
    ]
}}
""", broadcast)
        
        from tools.utils import extract_json
        sec_data = extract_json(sec_review)
        if sec_data and "issues" in sec_data:
            _review_results["issues"].extend(sec_data["issues"])

        # ── Stage 3: Efficiency Agent ────────────────────────────────────
        await broadcast({"type": "review_stage", "stage": "efficiency"})
        await broadcast({"type": "review_activity", "text": "Efficiency Agent checking for performance issues..."})

        eff_review = await _claude(f"""
Efficiency Agent: Review the following code for performance and efficiency.
Look for: Inefficient loops, redundant database calls, memory leaks, etc.
Code:
{diff_content[:3000]}

Output ONLY JSON:
{{
    "issues": [
        {{"type": "efficiency", "description": "...", "severity": "high/med/low"}}
    ]
}}
""", broadcast)
        
        eff_data = extract_json(eff_review)
        if eff_data and "issues" in eff_data:
            _review_results["issues"].extend(eff_data["issues"])

        # ── Stage 4: Logic Agent ──────────────────────────────────────
        await broadcast({"type": "review_stage", "stage": "review"})
        await broadcast({"type": "review_activity", "text": "Logic Agent checking for bugs..."})

        logic_review = await _claude(f"""
Logic Agent: Review the following code for logic bugs and edge cases.
Code:
{diff_content[:3000]}

Output ONLY JSON:
{{
    "summary": "overall feel",
    "issues": [
        {{"type": "logic", "description": "...", "severity": "high/med/low"}}
    ],
    "score": 85
}}
""", broadcast)
        
        logic_data = extract_json(logic_review)
        if logic_data:
            if "issues" in logic_data:
                _review_results["issues"].extend(logic_data["issues"])
            _review_results["summary"] = logic_data.get("summary", "Review completed.")
            _review_results["score"] = logic_data.get("score", 85)

        if _review_results:
            await broadcast({"type": "review_complete", "results": _review_results})

        # ── Stage 5: Report ───────────────────────────────────────────
        await broadcast({"type": "review_stage", "stage": "report"})
        
        report_text = f"🔍 *Multi-Agent Code Review Complete*\nProject: {target}\nScore: {_review_results.get('score', 'N/A')}/100\n\n"
        report_text += f"Summary: {_review_results.get('summary', '')}\n\n"
        
        for issue in _review_results.get('issues', []):
            icon = "🔴" if issue.get('severity') == 'high' else "🟡" if issue.get('severity') == 'med' else "🔵"
            report_text += f"{icon} [{issue['type']}] {issue['description']}\n"
        
        await send_telegram(report_text)
        
        # Log to Jarvis Hub
        try:
            from tools.logger import log_completed_task
            log_completed_task(
                task_name=f"Code Review: {target}",
                description=f"Multi-agent review for {target}. Score: {_review_results.get('score', 'N/A')}/100.",
                actions=[
                    f"Summary: {_review_results.get('summary', '')}",
                    f"Issues found: {len(_review_results.get('issues', []))}"
                ]
            )
            await broadcast({"type": "review_activity", "text": "Code review logged to Jarvis Hub."})
        except Exception as le:
            log.warning(f"Failed to log code review to Hub: {le}")

        await broadcast({"type": "review_activity", "text": "Workflow complete."})

    except Exception as e:
        log.exception("Review workflow error")
        await broadcast({"type": "review_error", "text": str(e)})
    finally:
        _running = False
