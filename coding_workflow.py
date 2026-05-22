"""
Coding Agent — senior-level dev skills.

Workflow: Understand → Plan → Lint → Edit → Test → Verify → Report

Handles:
  - Bug fixes       "fix the bug where X"
  - Features        "implement X in repo Y"
  - Code review     "review PR https://github.com/..."
  - Test writing    "write tests for X"
  - Refactoring     "refactor X to use Y"
  - Debug           "debug why X is failing"
  - GitHub issues   "fix issue #42 in repo X"
  - Codebase audit  "audit ~/my_project for security issues"
"""

import asyncio
import logging
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)
_running = False

AGENTIC_DIR  = Path(__file__).parent
VAULT        = Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain"
REVIEWS_DIR  = VAULT / "Projects & Building" / "Code Reviews"


# ── Prompt context ────────────────────────────────────────────────────────────

CODING_CONTEXT = """You are PAIS running as a senior software engineer for Taranveer Singh.

## Your engineering profile
- 5+ years full-stack experience (Python, TypeScript/React, Go)
- Deep Python expertise: FastAPI, Django, asyncio, type hints, pytest
- Frontend: React, Next.js, Tailwind
- Infra: AWS, Firebase, DigitalOcean, Docker
- Git-fluent: branches, PRs, rebasing, atomic commits

## Core workflow — ALWAYS follow this order
1. **Understand** — Read the task. Identify: language, framework, what is broken/missing.
2. **Recon** — Read relevant files BEFORE writing any code. Use Bash to explore the repo:
   ```bash
   python3 ~/agentic_os/tools/devtools.py tree <path>
   python3 ~/agentic_os/tools/devtools.py stats <path>
   ```
3. **Lint baseline** — Run ruff/mypy BEFORE editing to understand the current state:
   ```bash
   python3 -m ruff check <path> --output-format text
   python3 -m mypy <path> --ignore-missing-imports 2>&1 | head -40
   ```
4. **Plan** — Write a 3-5 point plan to your output before touching any file.
5. **Edit** — Make changes. One concern per edit. Prefer Edit over full rewrites.
6. **Verify** — After editing, re-run ruff and any tests:
   ```bash
   python3 -m ruff check <changed_file>
   python3 -m pytest <test_path> -v --tb=short 2>&1 | tail -30
   ```
7. **Report** — Summarize: what changed, why, any caveats.

## Static analysis tools
```bash
# Python linting (ruff — fast, supercedes flake8/pylint):
python3 -m ruff check <path>              # check
python3 -m ruff check <path> --fix        # auto-fix safe issues

# Type checking (mypy):
python3 -m mypy <path> --ignore-missing-imports

# JavaScript/TypeScript:
npx eslint <file>

# Tests:
python3 -m pytest <path> -v --tb=short    # Python
npm test                                   # JS/TS
```

## GitHub operations (use bash to call these)
```python
# All helpers are in tools/github_tools.py
import sys; sys.path.insert(0, '/Users/taranveersingh/agentic_os')
from tools.github_tools import (
    get_pr_diff, get_pr_metadata, get_pr_comments,
    list_issues, get_issue, get_file_content, search_code,
    get_repo_info, list_repo_files, create_pr, add_pr_comment,
)
```

## Task type dispatch

### Bug fix
1. Reproduce the bug (understand expected vs actual)
2. Run the existing tests to see what fails
3. Find root cause via code reading (don't guess)
4. Fix minimally — don't refactor unrelated code
5. Add a regression test if there's a test suite
6. Verify all tests pass

### Feature implementation
1. Read existing code to understand patterns (don't invent new ones)
2. Write the implementation + tests together
3. Run tests to verify
4. Note any TODOs or follow-ups

### Code review (PR URL given)
1. Fetch diff: `get_pr_diff(url)` and metadata: `get_pr_metadata(url)`
2. Read any referenced files for context: `get_file_content(...)`
3. Evaluate: correctness, edge cases, performance, security, style
4. Write a structured review: summary, issues, suggestions, verdict
5. Save review to vault

### Write tests
1. Read the module being tested first
2. Identify: happy paths, edge cases, error paths
3. Write tests using the existing test framework (pytest / jest)
4. Aim for ≥80% coverage of the target module
5. Run tests to verify they all pass

### Refactor
1. Run linter first to understand current issues
2. Make one change at a time (rename, extract, simplify)
3. Run tests after each change to catch regressions
4. Don't change behavior — only structure

### Debug
1. Reproduce the issue with a minimal example
2. Add logging/print to narrow down the location
3. Read the error traceback carefully — the answer is usually there
4. Fix and verify

### GitHub issue
1. Fetch issue details
2. Search the repo for relevant files: `search_code(owner, repo, issue_keywords)`
3. Read the relevant files
4. Plan → fix → test → propose PR (ask for approval before creating PR)

## Output — save to vault
For code reviews and audits, write a markdown report:
```bash
BASE="$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain"
mkdir -p "$BASE/Projects & Building/Code Reviews"
# File: $BASE/Projects & Building/Code Reviews/YYYY-MM-DD-<repo>-<type>.md
```

## Critical rules
- Never break working tests
- Never auto-push or auto-merge — stop before any git push
- Request approval before: creating PRs, committing to external repos, running destructive commands (rm -rf, drop table, etc.)
- If tests don't exist and the task requires them: write tests first, then implement (TDD)
- Write clean code — follow the existing style of the file you're editing
- When unsure, ask — don't guess and write code that might be wrong
"""


# ── Task classification ───────────────────────────────────────────────────────

_TASK_PATTERNS = {
    "review":    re.compile(r"\b(review|pr|pull.?request|diff)\b", re.I),
    "debug":     re.compile(r"\b(debug|traceback|error|exception|crash|failing|broken)\b", re.I),
    "test":      re.compile(r"\b(test|tests|pytest|jest|coverage|spec)\b", re.I),
    "feature":   re.compile(r"\b(implement|add|build|create|feature|endpoint|component)\b", re.I),
    "refactor":  re.compile(r"\b(refactor|clean|reorganize|improve|simplify|rename)\b", re.I),
    "audit":     re.compile(r"\b(audit|security|vulnerabilit|scan|check)\b", re.I),
    "fix":       re.compile(r"\b(fix|bug|patch|resolve|issue|problem)\b", re.I),
}

def _classify_task(task: str) -> str:
    for kind, pat in _TASK_PATTERNS.items():
        if pat.search(task):
            return kind
    return "general"


# ── Pre-flight context injection ──────────────────────────────────────────────

def _build_pre_context(task: str) -> str:
    """
    Detect if there's a local repo path or GitHub URL in the task.
    If yes, run recon tools and inject context into the prompt.
    If no explicit repo is named, default to the agentic_os repo so the
    agent doesn't have to guess which codebase to operate on.
    """
    lines: list[str] = []
    explicit_repo = False

    # Detect a local path
    path_match = re.search(r"(~/[^\s]+|/[^\s]+)", task)
    if path_match:
        raw_path = path_match.group(1)
        local_path = str(Path(raw_path).expanduser())
        if Path(local_path).exists():
            explicit_repo = True
            from tools.devtools import file_tree, language_stats, git_status
            lines.append(f"\n## Pre-flight: {local_path}")
            lines.append("### File tree")
            lines.append(file_tree(local_path, depth=3))
            lines.append("### Languages")
            lines.append(language_stats(local_path))
            lines.append("### Git status")
            lines.append(git_status(local_path))

    # Detect a GitHub PR URL
    pr_match = re.search(r"https://github\.com/[^\s]+/pull/\d+", task)
    if pr_match:
        try:
            from tools.github_tools import get_pr_diff, get_pr_metadata
            pr_url = pr_match.group(0)
            meta = get_pr_metadata(pr_url)
            diff = get_pr_diff(pr_url)
            lines.append(f"\n## PR Context: {meta['title']}")
            lines.append(f"Author: {meta['author']} | +{meta['additions']} -{meta['deletions']} | {meta['changed_files']} files")
            lines.append(f"Base: {meta['base']} ← Head: {meta['head']}")
            lines.append("\n### Diff (first 8000 chars)")
            lines.append(diff[:8000])
        except Exception as e:
            lines.append(f"\n## PR fetch failed: {e}")

    # Detect GitHub repo URL (non-PR)
    repo_match = re.search(r"https://github\.com/([^/\s]+)/([^/\s]+?)(?:\s|$)", task)
    if repo_match and "pull" not in task[repo_match.start():repo_match.end() + 10]:
        explicit_repo = True
        try:
            from tools.github_tools import get_repo_info, list_issues
            owner, repo = repo_match.group(1), repo_match.group(2).rstrip("/")
            info = get_repo_info(owner, repo)
            issues = list_issues(owner, repo, limit=8)
            from tools.github_tools import format_issues_for_prompt
            lines.append(f"\n## Repo Context: {info['name']}")
            lines.append(f"Language: {info['language']} | Stars: {info['stars']}")
            lines.append(f"Description: {info['description']}")
            if issues:
                lines.append("\n### Open issues (first 8)")
                lines.append(format_issues_for_prompt(issues))
        except Exception as e:
            lines.append(f"\n## Repo fetch failed: {e}")

    if pr_match:
        explicit_repo = True

    # Default repo: if the task doesn't name a path or GitHub repo, assume the
    # task is about the agentic_os codebase (PAIS itself). Without this the
    # agent has no target and may "fix" whatever dirty files it finds in cwd.
    if not explicit_repo:
        try:
            from tools.devtools import file_tree, git_status
            lines.append(f"\n## Default repo (no explicit path in task): {AGENTIC_DIR}")
            lines.append("Assume this task targets the PAIS codebase unless the task clearly refers to another project.")
            lines.append("Common areas: `dashboard/*.html` (UI pages incl. briefing, career, personal, trades), "
                         "`*_workflow.py` (agent workflows), `orchestrator.py`/`main.py` (routing), `tools/*` (shared utilities).")
            lines.append("### File tree")
            lines.append(file_tree(str(AGENTIC_DIR), depth=2))
            lines.append("### Git status")
            lines.append(git_status(str(AGENTIC_DIR)))
            lines.append("IMPORTANT: Do NOT 'fix' unrelated dirty files you see in git status — only touch files relevant to the task.")
        except Exception as e:
            lines.append(f"\n## Default repo context failed: {e}")

    # Inject RAG context
    try:
        from tools.rag import search
        rag = search(task, n_results=3)
        if rag:
            lines.append("\n## Vault context (may be relevant)")
            lines.append(rag[:1500])
    except Exception:
        pass

    return "\n".join(lines)


# ── Workflow entry ────────────────────────────────────────────────────────────

def is_running() -> bool:
    return _running


def _load_lessons(max_chars: int = 1200) -> str:
    lessons = AGENTIC_DIR / "lessons.md"
    if not lessons.exists() or lessons.stat().st_size == 0:
        return ""
    return "\n\n## Past Lessons\n" + lessons.read_text(encoding="utf-8")[-max_chars:]


async def run_coding_task(task_description: str, broadcast, send_telegram, sandbox_dir=None):
    global _running
    _running = True
    try:
        task_type = _classify_task(task_description)
        await broadcast({"type": "coding_activity", "text": f"[{task_type.upper()}] {task_description[:80]}"})

        # Inject pre-flight context (repo tree, diff, RAG)
        await broadcast({"type": "coding_activity", "text": "Reading codebase context..."})
        pre_ctx = await asyncio.to_thread(_build_pre_context, task_description)

        from tools.llm import run_llm_command

        full_prompt = (
            f"{CODING_CONTEXT}"
            f"{_load_lessons()}"
            f"{pre_ctx}\n\n"
            f"## Task [{task_type.upper()}]\n"
            f"{task_description}"
        )

        await broadcast({"type": "coding_activity", "text": f"Executing {task_type} task..."})

        res = await run_llm_command(
            prompt=full_prompt,
            broadcast=broadcast,
            send_telegram=send_telegram,
            sandbox_dir=sandbox_dir,
            agent_name="coding",
        )

        result = res.get("result", "Coding task complete.")

        # Save code reviews and audits to vault automatically
        if task_type in ("review", "audit"):
            await _save_review_to_vault(task_description, result, task_type)

        await broadcast({"type": "coding_activity", "text": "Done."})
        await send_telegram(f"[{task_type.upper()}] Coding task complete. Check activity for details.")
        return result

    except Exception as e:
        log.exception("Coding task error")
        await broadcast({"type": "coding_error", "text": str(e)})
        return f"Error: {e}"
    finally:
        _running = False


async def _save_review_to_vault(task: str, result: str, task_type: str):
    """Persist code reviews and audits to vault."""
    try:
        from datetime import datetime
        import re as _re
        REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        slug = _re.sub(r"[^\w]", "-", task[:40].lower()).strip("-")
        filepath = REVIEWS_DIR / f"{date_str}-{slug}.md"
        content = f"""---
tags:
  - code-review
  - {task_type}
---

# Code {task_type.title()} — {date_str}

**Task:** {task}

---

{result}
"""
        filepath.write_text(content, encoding="utf-8")
        log.info("Saved %s to vault: %s", task_type, filepath)
    except Exception as e:
        log.warning("Failed to save review to vault: %s", e)
