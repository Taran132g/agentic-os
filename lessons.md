# PAIS Lessons Learned

> Append-only log of self-corrections. Each entry records what went wrong,
> what was fixed, and the rule to follow next time.
> Read by all agents before every task.

---

## [2026-05-12] Career Ops jobs not persisting to page
**Feedback:** Previous career search found 4 jobs but they were not visible on career ops page after page refresh.
**Root cause:** Old career_workflow.py code had no `_save_jobs_cache()` — jobs were only broadcast over WebSocket (lost on reconnect/refresh). No cache file was created.
**Fix applied:** Cache saving code was already added to career_workflow.py (working tree changes). Triggered a fresh search via WebSocket to populate the cache. Jobs now persist in career_jobs_cache.json and are hydrated on page load.
**Lesson:** Always verify the career_jobs_cache.json file exists after a search; if missing, a new search must be triggered to repopulate it.

---

## [2026-05-14] Vault curator failed with 529 Overloaded error
**Feedback:** API error not your fault try again
**Root cause:** `tools/llm.py` retry logic only checked for usage-limit keywords; a transient 529 Overloaded response was not retried and surfaced as a hard failure.
**Fix applied:** Added `"529"` and `"overloaded"` to the `usage_keywords` list in `run_llm_command()` so transient server overload errors trigger the existing 30-second retry path.
**Lesson:** Include HTTP 529 / "overloaded" in the retry keyword list so transient API overloads are automatically retried rather than bubbling up as failures.

---

## [2026-05-15] Daily brief reported wrong BTC price
**Feedback:** BTC is not 103,400 have the briefing agent double check its output with multiple sources
**Root cause:** `DAILY_CONTEXT` in `briefing_workflow.py` had no instructions to verify market prices; the agent included a BTC figure from a single source (or memory) without cross-checking.
**Fix applied:** Added explicit rule to `DAILY_CONTEXT`: any market price must be confirmed by at least two separate WebSearch queries; if sources disagree by >2%, report a range instead of a single number.
**Lesson:** Never let the briefing agent state a market price without multi-source verification — require at least two WebSearch queries and report a range when sources conflict.

---

## [2026-05-15] Give me a quick bankroll summary — current balance and trade stats
**Feedback:** log the last dr profit signal I am waiting for entry but what should my sizing be the status should say waiting entry
**Root cause:** `add_trade()` in `tools/trade_tracker.py` hardcoded `status = "active"` — no "waiting_entry" state existed. The personal task agent also never used the trade tracker to log the vault-known May 6 BTC short signal; it only read vault data and returned a wall of text without writing to `trades.json`.
**Fix applied:** Added `status` parameter to `add_trade()` (defaults to `"active"` for backward compat). Manually logged the May 6 Dr. Profit BTC Short signal (entry zone $83K–$85K, SL $89K, TP $55K) with `status="waiting_entry"`.
**Lesson:** When Taran mentions an unlogged signal during any trade/bankroll task, immediately log it to trades.json via `add_trade(..., status="waiting_entry")` — don't just read the vault and return text.

---

## [2026-05-17] An agent just finished a task. Review its output and decide
**Feedback:** you are right check why this happened the coding agent didnt do any work
**Root cause:** `_build_pre_context()` in `coding_workflow.py` only injected repo recon when the task contained an explicit path or GitHub URL. For unscoped UI requests like "on the briefing page the text box should resize…", no codebase context was attached. The coding agent ran with `cwd=AGENTIC_DIR` but no signal about which files/repo to touch, so it grasped at the dirty git state it could see and "synced GEMINI.md with CLAUDE.md" instead of editing the briefing page.
**Fix applied:** `coding_workflow.py` — added a fallback in `_build_pre_context()`: when no explicit path or GitHub repo is named, inject the agentic_os file tree + git status as default context, plus a note that lists common areas (`dashboard/*.html`, `*_workflow.py`, `orchestrator.py`, `tools/*`) and an explicit instruction not to "fix" unrelated dirty files seen in git status.
**Lesson:** Coding tasks must always carry a concrete repo target to the LLM. If the user doesn't name one, default to the PAIS repo and tell the agent which dirty files to ignore — never let it pick a target from ambient repo state.

---
