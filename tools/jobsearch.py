"""
Real job search via free public APIs (no auth required).

The Muse API: tech/engineering jobs with real application URLs.
Remotive API: remote tech jobs with real application URLs.

Returns structured job dicts compatible with career_workflow.py.
"""

import logging
import re
import time
import urllib.parse
from typing import Optional

import requests

log = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers["User-Agent"] = "PAIS-Career-Agent/1.0"


# ── The Muse ──────────────────────────────────────────────────────────────────

MUSE_URL = "https://www.themuse.com/api/public/jobs"
# Only "Software Engineering" has internship listings; search across both pages
# and let keyword scoring rank relevance post-fetch.
MUSE_INTERN_CATEGORY = "Software Engineering"

def _muse_search(keywords: str, limit: int = 6) -> list[dict]:
    kw_lower = keywords.lower()

    results = []
    for page in range(3):
        try:
            resp = _SESSION.get(
                MUSE_URL,
                params={"category": MUSE_INTERN_CATEGORY, "level": "Internship", "page": page, "descending": True},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.warning("Muse API error (page %d): %s", page, e)
            break

        for job in data.get("results", []):
            url = job.get("refs", {}).get("landing_page", "")
            if not url or not url.startswith("http"):
                continue

            name = job.get("name", "")
            company = job.get("company", {}).get("name", "Unknown")
            locations = job.get("locations", [])
            location = locations[0].get("name", "Remote") if locations else "Remote"
            short_desc = job.get("contents", "")[:600]

            # Relevance score: prefer keyword match in title
            score = 70
            if any(k in name.lower() for k in kw_lower.split()):
                score += 15
            if "intern" in name.lower():
                score += 10

            results.append({
                "source": "muse",
                "company": company,
                "role": name,
                "url": url,
                "location": location,
                "match_score": min(score, 95),
                "jd_summary": _truncate(short_desc, 300),
            })

            if len(results) >= limit:
                break

        if len(results) >= limit or not data.get("results"):
            break

        time.sleep(0.3)

    return results[:limit]


# ── Remotive ──────────────────────────────────────────────────────────────────

REMOTIVE_URL = "https://remotive.com/api/remote-jobs"

def _remotive_search(keywords: str, limit: int = 6) -> list[dict]:
    kw_lower = keywords.lower()
    try:
        resp = _SESSION.get(
            REMOTIVE_URL,
            params={"category": "software-dev", "search": keywords, "limit": limit * 2},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning("Remotive API error: %s", e)
        return []

    results = []
    for job in data.get("jobs", []):
        url = job.get("url", "")
        if not url or not url.startswith("http"):
            continue

        title = job.get("title", "")
        company = job.get("company_name", "Unknown")
        desc = _strip_html(job.get("description", ""))[:600]

        # Only include roles that are clearly intern/junior/entry-level
        title_lower = title.lower()
        is_entry = any(w in title_lower for w in ("intern", "junior", "entry", "graduate", "associate", "apprentice"))
        if not is_entry:
            continue

        score = 72
        if any(k in title_lower for k in kw_lower.split()):
            score += 12
        if "intern" in title_lower:
            score += 8

        results.append({
            "source": "remotive",
            "company": company,
            "role": title,
            "url": url,
            "location": "Remote",
            "match_score": min(score, 95),
            "jd_summary": _truncate(desc, 300),
        })

        if len(results) >= limit:
            break

    return results[:limit]


# ── Public entry point ────────────────────────────────────────────────────────

def search_jobs(keywords: str, max_results: int = 8) -> list[dict]:
    """
    Search for real job listings matching keywords.
    Returns list of job dicts with verified URLs from real job boards.
    """
    log.info("Job search: '%s'", keywords)
    jobs: list[dict] = []

    # Fetch from both sources in sequence (free tier, no parallelism needed)
    jobs += _muse_search(keywords, limit=max_results // 2 + 1)
    jobs += _remotive_search(keywords, limit=max_results // 2 + 1)

    # Deduplicate by company+role
    seen: set[str] = set()
    unique: list[dict] = []
    for j in jobs:
        key = f"{j['company'].lower()}:{j['role'].lower()}"
        if key not in seen:
            seen.add(key)
            unique.append(j)

    # Sort by match score desc, return top N
    unique.sort(key=lambda x: x["match_score"], reverse=True)

    # Assign sequential IDs
    for i, j in enumerate(unique[:max_results]):
        j["id"] = f"job_{i+1}"
        j.setdefault("match_reason", f"Found via {j.get('source','api')} — matches '{keywords}'")

    log.info("Job search found %d results", len(unique[:max_results]))
    return unique[:max_results]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _truncate(text: str, n: int) -> str:
    return text[:n] + "…" if len(text) > n else text

def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text).strip()
