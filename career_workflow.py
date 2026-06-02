"""
Career Ops pipeline (browser-agent era):
  1. Scout — search The Muse + Remotive (with Claude WebSearch fallback)
  2. Fill  — tools/browser_fill.py drives an in-Chrome agent (Gemini by
            default, Claude-for-Chrome as fallback) to fill each application
            inside Taran's real Chrome. No tailoring; one resume PDF used
            everywhere (~/agentic_os/resume.pdf symlink).
  3. Telegram notification so Taran reviews and submits himself.

Playwright is no longer used. Stage 2 (per-job LLM resume tailoring +
per-job PDF render) was removed on 2026-05-25 per Taran's directive:
"use this resume, don't tailor anything for different places."

Default agent switched from Claude-for-Chrome to Gemini-in-Chrome on
2026-05-25 — Gemini's toolbar button is text-labeled (OCR-friendly) and
its quota is separate from Taran's Claude subscription.
"""

import asyncio
import json
import logging
import re
from pathlib import Path

from tools.tracker import save_application

log = logging.getLogger(__name__)

AGENTIC_DIR = Path(__file__).parent
VAULT_RESUME = (
    Path.home()
    / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain"
    / "About Taran/Resume.md"
)
JOBS_CACHE = AGENTIC_DIR / "career_jobs_cache.json"

_running = False
_jobs: list[dict] = []


def _save_jobs_cache():
    """Persist current _jobs list to disk so the API can serve them after WS reconnect."""
    try:
        JOBS_CACHE.write_text(json.dumps(_jobs, ensure_ascii=False, indent=2))
    except Exception as e:
        log.warning("Failed to save career jobs cache: %s", e)


def is_running() -> bool:
    return _running


def get_jobs() -> list[dict]:
    return list(_jobs)


async def _claude(prompt: str, broadcast) -> str:
    from tools.llm import run_llm_command
    res = await run_llm_command(
        prompt=prompt,
        broadcast=broadcast,
        allowed_tools="WebSearch,WebFetch,Read"
    )
    return res["result"]


VAULT_CAREER_DIR = (
    Path.home()
    / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain"
    / "Career"
)


async def _save_application_to_vault(job: dict, fill_result: dict):
    """Write a markdown file for this application to the vault."""
    try:
        VAULT_CAREER_DIR.mkdir(parents=True, exist_ok=True)
        company_slug = re.sub(r"[^\w]", "_", job.get("company", "unknown"))
        filepath = VAULT_CAREER_DIR / f"{company_slug}_{job.get('id', 'job')}.md"

        bullets = job.get("tailored", {}).get("bullets", [])
        bullet_lines = "\n".join(
            f"- **{b.get('section','')}: ** {b.get('tailored', b.get('original', ''))}"
            for b in bullets
        )
        fields = fill_result.get("fields_filled", [])
        field_lines = "\n".join(f"- `{f['field']}`: {f['value']}" for f in fields)
        err = fill_result.get("error", "")
        hook = job.get("tailored", {}).get("hook", "")

        content = f"""---
tags:
  - career
  - application
---

# {job.get('company')} — {job.get('role')}

**Location:** {job.get('location', '?')}
**URL:** {job.get('url', '?')}
**Match Score:** {job.get('match_score', '?')}
**Match Reason:** {job.get('match_reason', '')}

---

## Cover Letter Hook
{hook}

---

## Tailored Resume Bullets
{bullet_lines or '_No tailored bullets_'}

---

## Application Fields Filled ({len(fields)} total)
{field_lines or '_No fields filled_'}

{f'**Error during fill:** {err}' if err else ''}

---

## Status
- [ ] Reviewed
- [ ] Submitted
"""
        filepath.write_text(content, encoding="utf-8")
        log.info("Saved application details to vault: %s", filepath)
    except Exception as e:
        log.warning("Failed to save application to vault: %s", e)


async def run_career_search(keywords: str, broadcast, send_telegram):
    global _running, _jobs
    _running = True
    _jobs = []
    from tools.utils import extract_json

    try:
        resume = VAULT_RESUME.read_text() if VAULT_RESUME.exists() else "Resume not found."

        # ── Stage 1: Search (real job APIs) ───────────────────────────
        await broadcast({"type": "career_stage", "stage": "search"})
        await broadcast({"type": "career_activity", "text": f'Searching job boards for: "{keywords}"'})

        import asyncio as _asyncio
        from tools.jobsearch import search_jobs
        jobs_raw = await _asyncio.to_thread(search_jobs, keywords, 6)

        if not jobs_raw:
            await broadcast({"type": "career_activity",
                             "text": "Job APIs returned no results — trying web search fallback…"})
            from tools.utils import extract_json as _ej
            search_result = await _claude(f"""
Use WebSearch to find "{keywords}" internship openings on LinkedIn, Handshake, and company career pages.
For each job found, verify the application URL exists using WebFetch before including it.

Find 4 strong matches for Taran. Output ONLY a JSON array:
```json
[{{
  "id": "job_1",
  "company": "Company Name",
  "role": "Exact Role Title",
  "url": "direct application URL you verified exists",
  "location": "City ST or Remote",
  "match_score": 88,
  "match_reason": "One sentence on why this fits Taran",
  "jd_summary": "Top 3 requirements from the JD"
}}]
```

Taran: Penn State AI Engineering (2024–2028), Python/AWS/Firebase/Flask/Django/React/ROS2,
EV charging IoT intern, DeFi Club Trading Lead, co-founded Piontrix.
IMPORTANT: Only include URLs you confirmed are real application pages via WebFetch.
""", broadcast)
            jobs_raw = _ej(search_result)
            if not isinstance(jobs_raw, list):
                jobs_raw = []

        for j in jobs_raw:
            j.setdefault("status", "found")
            _jobs.append(j)
            await broadcast({"type": "career_job_found", "job": dict(j)})
        _save_jobs_cache()

        if not _jobs:
            await broadcast({"type": "career_activity", "text": "No structured job data parsed — check activity log above."})
            return

        # ── Stage 2: Fill via Claude for Chrome ───────────────────────
        # Old Stage 2 (LLM bullet tailoring + per-job PDF) removed 2026-05-25
        # per Taran's directive: one resume, no per-job tailoring. The current
        # resume lives at ~/agentic_os/resume.pdf (symlink to ~/Downloads/...).
        await broadcast({"type": "career_stage", "stage": "fill"})

        for job in _jobs:
            url = job.get("url", "")
            if not url or not url.startswith("http"):
                job["status"] = "no_url"
                await broadcast({"type": "career_job_update", "job_id": job["id"], "status": "no_url"})
                await broadcast({"type": "career_activity", "text": f"No application URL for {job['company']} — skipping"})
                continue

            job["status"] = "filling"
            await broadcast({"type": "career_filling", "job_id": job["id"], "company": job["company"], "url": url})
            await broadcast({"type": "career_activity", "text": f"Opening {job['company']} in Chrome and pasting brief to Gemini..."})

            try:
                from tools.browser_fill import browser_fill

                # Fire-and-verify: paste brief → Start task → 60s verify → return.
                # No autonomous correction loops (token-light). The workflow sends
                # its own per-job Telegram below, so browser_fill stays quiet here.
                # Taran reviews the open Chrome tab, fixes small errors, and submits.
                fill_result = await asyncio.to_thread(browser_fill, job, notify=False)

            except Exception as e:
                log.exception("browser_fill failed for %s", job["company"])
                fill_result = {"ok": False, "error": str(e),
                               "platform": "gemini", "fields_filled": [],
                               "screenshot_b64": "", "screenshot_bytes": b""}

            platform = fill_result.get("platform", "gemini")

            job["status"] = "needs_review"
            # Strip non-JSON-serializable bytes before persisting to the cache.
            job["fill_result"] = {k: v for k, v in fill_result.items() if k != "screenshot_bytes"}
            job["screenshot_b64"] = fill_result.get("screenshot_b64", "")
            job["platform"] = platform
            _save_jobs_cache()

            # Save to application tracker
            try:
                save_application(job, status="pending", platform=platform)
            except Exception as te:
                log.warning("Tracker save failed: %s", te)

            await broadcast({
                "type": "career_needs_review",
                "job_id": job["id"],
                "company": job["company"],
                "role": job["role"],
                "url": url,
                "platform": platform,
                "fields_filled": fill_result.get("fields_filled", []),
                "screenshot_b64": fill_result.get("screenshot_b64", ""),
                "error": fill_result.get("error", ""),
            })

            status = fill_result.get("status", "")
            err = fill_result.get("error", "")
            engaged = fill_result.get("ok") and status == "running"
            status_line = ("Gemini is filling — review & submit"
                           if engaged else f"Needs a look: {err[:80] or status}")

            if engaged:
                caption = (
                    f"✅ *{job['company']}* — {job['role']}\n"
                    f"📍 {job.get('location', '?')}\n"
                    f"Gemini is filling the form (via {platform}).\n\n"
                    f"Review the Chrome tab, fix any small errors, upload the "
                    f"resume, and submit manually."
                )
            else:
                caption = (
                    f"⚠️ *{job['company']}* — {job['role']}\n"
                    f"📍 {job.get('location', '?')}\n"
                    f"Start task may not have engaged ({err[:120] or status}).\n\n"
                    f"Open the Chrome window and check / click Start task."
                )

            await broadcast({"type": "career_activity", "text": f"✓ {job['company']} — {status_line}"})

            # Save application details to vault
            await _save_application_to_vault(job, fill_result)

            await send_telegram(caption)

            # Send the final screenshot via Telegram if we captured one
            screenshot_bytes = fill_result.get("screenshot_bytes", b"")
            if screenshot_bytes:
                try:
                    import telegram_bot as _tg
                    await _tg.send_photo(
                        screenshot_bytes,
                        caption=f"{job['company']} — {job['role']} (final state)",
                    )
                except Exception as photo_err:
                    log.warning("Could not send screenshot photo: %s", photo_err)

        # Log completion to PAIS Hub
        try:
            from tools.logger import log_completed_task
            log_completed_task(
                task_name=f"Career Search: {keywords}",
                description=f"Job search + in-Chrome browser-agent fill for: {keywords}",
                actions=[
                    f"Found {len(_jobs)} jobs",
                    "Pasted application brief to the in-Chrome agent (Gemini) for each",
                    "Filled applications in Chrome (stopped before submit)"
                ]
            )
        except Exception as le:
            log.warning(f"Failed to log career search to Hub: {le}")

        await broadcast({"type": "career_stage", "stage": "review"})

        job_lines = "\n".join(
            f"- {j['company']} — {j['role']} ({j.get('location','?')})" for j in _jobs
        )
        return f"Found {len(_jobs)} job(s) matching '{keywords}':\n{job_lines}\n\nThe in-Chrome agent (Gemini) was driven to fill each application. Review the Chrome tabs and submit manually."

    except Exception as e:
        log.exception("Career workflow error")
        await broadcast({"type": "career_error", "text": str(e)})
        return f"Career search error: {e}"
    finally:
        _running = False
