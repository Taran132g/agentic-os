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

            tailored = extract_json(tailor_result)
            job["tailored"] = tailored or {}
            job["status"] = "tailored"

            # Render a per-job tailored resume PDF (career_resumes/<id>.pdf).
            try:
                from tools.resume_pdf import generate_tailored_resume
                pdf = await asyncio.to_thread(generate_tailored_resume, job)
                job["resume_ready"] = bool(pdf)
                if pdf:
                    await broadcast({"type": "career_activity",
                                     "text": f"Tailored resume rendered for {job['company']}"})
            except Exception as re_err:
                job["resume_ready"] = False
                log.warning("Tailored resume failed for %s: %s", job.get("company"), re_err)

            await broadcast({"type": "career_tailored", "job_id": job["id"],
                             "tailored": job["tailored"], "resume_ready": job.get("resume_ready", False)})
            _save_jobs_cache()

        # ── Stage 3: Fill applications ────────────────────────────────
        await broadcast({"type": "career_stage", "stage": "fill"})

        # Generate resume PDF if not already on disk
        resume_pdf_path = AGENTIC_DIR / "resume.pdf"
        if not resume_pdf_path.exists():
            await broadcast({"type": "career_activity", "text": "Generating resume PDF for uploads..."})
            try:
                from tools.resume_pdf import generate_resume_pdf
                await asyncio.to_thread(generate_resume_pdf)
                await broadcast({"type": "career_activity", "text": "✓ Resume PDF generated"})
            except Exception as pdf_err:
                await broadcast({"type": "career_activity",
                                 "text": f"Resume PDF skipped (file uploads will be manual): {pdf_err}"})

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

            platform = fill_result.get("platform", "generic")
            needs_login = fill_result.get("needs_manual_login", False)

            job["status"] = "needs_review"
            # Strip non-JSON-serializable bytes before persisting — raw
            # screenshot_bytes blows up _save_jobs_cache().
            job["fill_result"] = {k: v for k, v in fill_result.items() if k != "screenshot_bytes"}
            job["screenshot_b64"] = fill_result.get("screenshot_b64", "")
            job["platform"] = platform
            job["needs_manual_login"] = bool(needs_login)
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
                "needs_manual_login": needs_login,
                "fields_filled": fill_result.get("fields_filled", []),
                "screenshot_b64": fill_result.get("screenshot_b64", ""),
                "error": fill_result.get("error", ""),
            })

            n_filled = len(fill_result.get("fields_filled", []))
            err = fill_result.get("error", "")

            if needs_login:
                status_line = f"Workday — browser open, manual login required"
                caption = (
                    f"⚠️ *Workday detected — manual login required*\n"
                    f"*{job['company']}* — {job['role']}\n"
                    f"📍 {job.get('location', '?')}\n\n"
                    f"Browser is open. Sign in, complete the application, "
                    f"then mark as Applied in the Career dashboard."
                )
            else:
                status_line = f"{n_filled} fields filled [{platform}]" if not err else f"Error: {err[:80]}"
                caption = (
                    f"✅ *Application ready — review & submit*\n"
                    f"*{job['company']}* — {job['role']}\n"
                    f"📍 {job.get('location', '?')}\n"
                    f"Fields filled: {n_filled} · Platform: {platform}\n\n"
                    f"Review on Career page, then submit manually."
                )

            await broadcast({"type": "career_activity", "text": f"✓ {job['company']} ready for review — {status_line}"})

            # Save application details to vault
            await _save_application_to_vault(job, fill_result)

            await send_telegram(caption)

            # Send screenshot photo if we have one
            screenshot_bytes = fill_result.get("screenshot_bytes", b"")
            if screenshot_bytes:
                try:
                    import telegram_bot as _tg
                    await _tg.send_photo(
                        screenshot_bytes,
                        caption=f"{job['company']} — {job['role']} (filled form screenshot)",
                    )
                except Exception as photo_err:
                    log.warning("Could not send screenshot photo: %s", photo_err)

        # Log completion to PAIS Hub
        try:
            from tools.logger import log_completed_task
            log_completed_task(
                task_name=f"Career Search: {keywords}",
                description=f"Automated job search and resume tailoring for: {keywords}",
                actions=[
                    f"Found {len(_jobs)} jobs",
                    "Tailored resumes for all matches",
                    "Filled applications in browser (stopped before submit)"
                ]
            )
        except Exception as le:
            log.warning(f"Failed to log career search to Hub: {le}")

        await broadcast({"type": "career_stage", "stage": "review"})

        job_lines = "\n".join(
            f"- {j['company']} — {j['role']} ({j.get('location','?')})" for j in _jobs
        )
        return f"Found {len(_jobs)} job(s) matching '{keywords}':\n{job_lines}\n\nResumes tailored and applications pre-filled. Review on Career page before submitting."

    except Exception as e:
        log.exception("Career workflow error")
        await broadcast({"type": "career_error", "text": str(e)})
        return f"Career search error: {e}"
    finally:
        _running = False
