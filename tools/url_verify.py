#!/usr/bin/env python3
"""Deterministic HTTP verification for scouted job URLs.

The job scouts ask an LLM (`claude -p` + WebFetch) to "verify each application URL
is live." That claim is unreliable — the model returns plausible-but-dead URLs:
right domain and path pattern, hallucinated or expired req ID (e.g. a PwC posting
that actually 404s). Those sailed into the fill queue and surfaced on Telegram as
"could not fill / site not found."

This module does the check the LLM can't be trusted to do: actually GET each URL
and drop the ones that are dead or clearly closed BEFORE they reach the pipeline.

Design choices:
- **Fail OPEN on our own errors.** A transient network blip on this machine must
  not nuke a whole scout — only a clean signal from the *remote* server (a 404, or
  an explicit "no longer accepting" banner) drops a job.
- **Don't require the role text to be present.** Many live ATS pages (Workday,
  iCIMS) are JS shells whose role text isn't in the raw HTML; requiring it would
  wrongly discard live postings. Status code + closed-phrases + redirect-to-root
  catch the reported failure ("site not found") without that false-drop.
"""

from __future__ import annotations

import socket
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

# Server-rendered "this posting is gone" phrases. SPAs won't contain these — that's
# fine; we only DROP on a positive signal, never on absence.
_CLOSED_SIGNALS = (
    "no longer accepting", "no longer available", "no longer open",
    "position closed", "position has been filled", "posting has closed",
    "this job has expired", "requisition is closed", "job not found",
    "page not found", "jobs you may be interested in instead",
)

# Bare careers/home roots a dead posting commonly redirects to. Normalized as
# path.rstrip("/").lower() — so "/" and "" both become "".
_GENERIC_PATHS = frozenset({"", "/careers", "/jobs", "/en", "/en-us",
                            "/search", "/job-search", "/home"})

_MAX_BODY = 200_000     # cap the read; closed banners live near the top anyway
_TIMEOUT = 20           # per-URL seconds


def _norm_path(u: str) -> str:
    return urlparse(u).path.rstrip("/").lower()


def url_is_open(job_or_url) -> bool:
    """True if the URL is live and not obviously closed. Fail-open on our errors."""
    url = job_or_url.get("url", "") if isinstance(job_or_url, dict) else str(job_or_url)
    url = str(url or "")
    if not url.startswith("http"):
        return False

    req = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Accept": "text/html,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            final = resp.geturl()
            status = getattr(resp, "status", 200) or 200
            body = resp.read(_MAX_BODY).decode("utf-8", "ignore").lower()
    except urllib.error.HTTPError as e:
        # A clean signal from the remote server. 404/410 = gone → drop. Other codes
        # (403 bot-wall, 429, 5xx) are ambiguous → keep (fail-open).
        return e.code not in (404, 410)
    except urllib.error.URLError as e:
        # DNS failure or refused connection = the host doesn't exist / isn't
        # serving (a hallucinated or dead domain) → drop. A timeout is ambiguous
        # (could be our network or a slow server) → keep (fail-open).
        reason = getattr(e, "reason", None)
        if isinstance(reason, (socket.gaierror, ConnectionRefusedError)):
            return False
        return True
    except Exception:
        return True                                  # our side failed → keep

    if status >= 400:
        return False
    # Redirected off the specific posting onto a bare careers/home root → req gone.
    if _norm_path(final) in _GENERIC_PATHS and _norm_path(url) not in _GENERIC_PATHS:
        return False
    if any(sig in body[:20_000] for sig in _CLOSED_SIGNALS):
        return False
    return True


def filter_open(jobs: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split jobs into (kept, dropped) by live-URL check, verified concurrently.
    Order of `kept` matches the input. `dropped` is for logging/visibility."""
    cands = [j for j in jobs
             if isinstance(j, dict) and str(j.get("url", "")).startswith("http")]
    if not cands:
        return [], []
    with ThreadPoolExecutor(max_workers=min(6, len(cands))) as ex:
        alive = list(ex.map(url_is_open, cands))
    kept = [j for j, ok in zip(cands, alive) if ok]
    dropped = [j for j, ok in zip(cands, alive) if not ok]
    return kept, dropped
