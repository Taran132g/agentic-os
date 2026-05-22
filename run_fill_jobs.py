"""
Standalone script to run Playwright form filler for all cached jobs.
Run from agentic_os directory.
"""
import json
import sys
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

AGENTIC_DIR = Path(__file__).parent
JOBS_CACHE = AGENTIC_DIR / "career_jobs_cache.json"

sys.path.insert(0, str(AGENTIC_DIR))

from tools.playwright_apply import fill_application

def main():
    jobs = json.loads(JOBS_CACHE.read_text())
    log.info("Loaded %d jobs from cache", len(jobs))

    for job in jobs:
        url = job.get("url", "")
        if not url or not url.startswith("http"):
            log.warning("No URL for %s — skipping", job.get("company"))
            continue

        log.info("=== Filling: %s — %s ===", job["company"], job["role"])
        log.info("URL: %s", url)

        result = fill_application(job)

        job["fill_result"] = {
            "fields_filled": result.get("fields_filled", []),
            "error": result.get("error", ""),
        }
        job["screenshot_b64"] = result.get("screenshot_b64", "")
        job["status"] = "needs_review"

        n = len(result.get("fields_filled", []))
        err = result.get("error", "")
        if err:
            log.warning("  Error: %s", err)
        else:
            log.info("  Filled %d fields", n)
            for f in result.get("fields_filled", []):
                log.info("    %s: %s", f["field"], f["value"])

    JOBS_CACHE.write_text(json.dumps(jobs, ensure_ascii=False, indent=2))
    log.info("Cache updated with fill results.")

    print("\n--- SUMMARY ---")
    for job in jobs:
        fr = job.get("fill_result", {})
        n = len(fr.get("fields_filled", []))
        err = fr.get("error", "")
        status = f"{n} fields filled" if not err else f"ERROR: {err[:80]}"
        print(f"  {job['company']}: {status}")

if __name__ == "__main__":
    main()
