"""
Career Ops pipeline:
  1. Claude searches for matching jobs (web search + JD scraping)
  2. Claude tailors resume bullets per job
  3. Playwright opens the application URL, fills the form, stops before submit
  4. Telegram notification so Taran can review and submit himself
"""

import asyncio
import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

AGENTIC_DIR = Path(__file__).parent
VAULT_RESUME = (
    Path.home()
    / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain"
    / "About Taran/Resume.md"
)

_running = False
_jobs: list[dict] = []


def is_running() -> bool:
    return _running


def get_jobs() -> list[dict]:
    return list(_jobs)


def _extract_json(text: str) -> list | dict | None:
    m = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    for ch in ("[", "{"):
        start = text.find(ch)
        if start == -1:
            continue
        for end in range(len(text), start, -1):
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                continue
    return None


async def _claude(prompt: str, broadcast) -> str:
    cmd = [
        "claude", "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--strict-mcp-config",
        "--mcp-config", '{"mcpServers":{}}',
        "--allowedTools", "WebSearch,WebFetch,Read",
        "--dangerously-skip-permissions",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(AGENTIC_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=10 * 1024 * 1024,
    )
    result = ""
    async for raw in proc.stdout:
        line = raw.decode().strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
            if ev.get("type") == "result":
                result = ev.get("result", "")
            elif ev.get("type") == "assistant":
                for b in ev.get("message", {}).get("content", []):
                    if b.get("type") == "text" and b["text"].strip():
                        await broadcast({"type": "career_activity", "text": b["text"].strip()[:400]})
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    await proc.wait()
    return result


async def run_career_search(keywords: str, broadcast, send_telegram):
    global _running, _jobs
    _running = True
    _jobs = []

    try:
        resume = VAULT_RESUME.read_text() if VAULT_RESUME.exists() else "Resume not found."

        # ── Stage 1: Search ───────────────────────────────────────────
        await broadcast({"type": "career_stage", "stage": "search"})
        await broadcast({"type": "career_activity", "text": f'Searching: "{keywords}"'})

        search_result = await _claude(f"""
Search for "{keywords}" internship openings posted in the last 60 days on LinkedIn, Handshake, company career pages, and job boards.

Find 4 strong matches for Taran's profile. Output ONLY a JSON array — no other text:
```json
[
  {{
    "id": "job_1",
    "company": "Company Name",
    "role": "Exact Role Title",
    "url": "direct application URL (not a search results page)",
    "location": "City ST or Remote",
    "match_score": 88,
    "match_reason": "One sentence on why this fits Taran",
    "jd_summary": "Top 3 requirements from the JD in one sentence each"
  }}
]
```

Taran's profile: Penn State AI Engineering student (2024–2027), Python/AWS/Firebase/Flask/Django/React/ROS2, built trading bots on Schwab, EV charging IoT intern (AWS+ROS2), Penn State DeFi Club Trading Lead, co-founded Piontrix tech consulting startup. Looking for AI/ML/software engineering internships.
""", broadcast)

        jobs_raw = _extract_json(search_result)
        if not isinstance(jobs_raw, list):
            jobs_raw = []

        for j in jobs_raw:
            j.setdefault("status", "found")
            _jobs.append(j)
            await broadcast({"type": "career_job_found", "job": dict(j)})

        if not _jobs:
            await broadcast({"type": "career_activity", "text": "No structured job data parsed — check activity log above."})
            return

        # ── Stage 2: Tailor resume ────────────────────────────────────
        await broadcast({"type": "career_stage", "stage": "tailor"})

        for job in _jobs:
            job["status"] = "tailoring"
            await broadcast({"type": "career_job_update", "job_id": job["id"], "status": "tailoring"})
            await broadcast({"type": "career_activity", "text": f"Tailoring resume for {job['company']} — {job['role']}..."})

            tailor_result = await _claude(f"""
Taran's resume:
{resume[:3000]}

Target role: {job['role']} at {job['company']}
Job requirements: {job.get('jd_summary', 'Not specified')}

Reframe Taran's existing experience bullets to match this role's language and emphasis. Do NOT invent anything — only rephrase, reorder, or emphasize what is already true.

Output ONLY JSON:
```json
{{
  "bullets": [
    {{"original": "old bullet text", "tailored": "new bullet text", "section": "Experience or Projects"}}
  ],
  "hook": "One-line cover letter opener specific to this company and role"
}}
```
""", broadcast)

            tailored = _extract_json(tailor_result)
            job["tailored"] = tailored or {}
            job["status"] = "tailored"
            await broadcast({"type": "career_tailored", "job_id": job["id"], "tailored": job["tailored"]})

        # ── Stage 3: Fill applications ────────────────────────────────
        await broadcast({"type": "career_stage", "stage": "fill"})

        for job in _jobs:
            url = job.get("url", "")
            if not url or not url.startswith("http"):
                job["status"] = "no_url"
                await broadcast({"type": "career_job_update", "job_id": job["id"], "status": "no_url"})
                await broadcast({"type": "career_activity", "text": f"No application URL for {job['company']} — skipping browser fill"})
                continue

            job["status"] = "filling"
            await broadcast({"type": "career_filling", "job_id": job["id"], "company": job["company"], "url": url})
            await broadcast({"type": "career_activity", "text": f"Opening browser for {job['company']}..."})

            try:
                from tools.playwright_apply import fill_application
                fill_result = await asyncio.to_thread(fill_application, job)
            except Exception as e:
                log.exception("Playwright fill failed for %s", job["company"])
                fill_result = {"error": str(e), "fields_filled": [], "screenshot_b64": ""}

            job["status"] = "needs_review"
            job["fill_result"] = fill_result
            job["screenshot_b64"] = fill_result.get("screenshot_b64", "")

            await broadcast({
                "type": "career_needs_review",
                "job_id": job["id"],
                "company": job["company"],
                "role": job["role"],
                "url": url,
                "fields_filled": fill_result.get("fields_filled", []),
                "screenshot_b64": fill_result.get("screenshot_b64", ""),
                "error": fill_result.get("error", ""),
            })

            n_filled = len(fill_result.get("fields_filled", []))
            err = fill_result.get("error", "")
            status_line = f"{n_filled} fields filled" if not err else f"Error: {err[:80]}"
            await broadcast({"type": "career_activity", "text": f"✓ {job['company']} ready for review — {status_line}"})

            await send_telegram(
                f"✅ *Application ready — review & submit*\n"
                f"*{job['company']}* — {job['role']}\n"
                f"📍 {job.get('location', '?')}\n\n"
                f"Fields filled: {n_filled}\n"
                f"Browser is open on your screen. Review and click Submit when ready."
            )

        await broadcast({"type": "career_stage", "stage": "review"})

    except Exception as e:
        log.exception("Career workflow error")
        await broadcast({"type": "career_error", "text": str(e)})
    finally:
        _running = False
