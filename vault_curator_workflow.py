"""
Vault Curator Agent — maintains and improves the Digital Brain.
Finds orphans, adds cross-links, writes synthesis pages, keeps the graph healthy.
"""

import logging
from pathlib import Path

log = logging.getLogger(__name__)
_running = False

AGENTIC_DIR = Path(__file__).parent
VAULT = Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain"

CURATOR_CONTEXT = f"""You are PAIS curating Taran's Obsidian Digital Brain vault.

## Vault location
{str(VAULT)}

## Your role
You are the gardener of this wiki. You keep it healthy, connected, and growing.
The vault is Taran's compounding knowledge base — every connection you add makes it more valuable.

## Always start by reading the index
```bash
BASE="$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain"
cat "$BASE/index.md"
cat "$BASE/log.md" | tail -40
```

## Task dispatch logic

### "lint" / "maintenance" / "full check"
Full maintenance pass — do all of the following:
1. Find orphaned notes (no inbound links from other notes)
2. Find notes with missing parent breadcrumbs
3. Find notes that reference concepts not yet having their own page
4. Check that hub pages link to all their children
5. Fix any issues you find

### "orphans" / "find orphans"
1. List all .md files in the vault
2. For each file, check how many other files link to it with [[filename]]
3. Return a list of files with 0 or 1 inbound links
4. For each orphan, either:
   - Add links from appropriate parent/hub pages
   - Suggest a merge if the content overlaps another note

### "cross-link [topic]"
Find all notes related to [topic] and add wikilinks between them where missing.

### "synthesize [topic]"
Write a new synthesis page that connects multiple existing notes on a theme.
Example: "synthesize trading psychology" would connect TNFund notes + Dr. Profit notes
+ Psychology notes into a new synthesis page.

### "update index"
Read all vault files and update index.md to reflect current state.

### "stale check"
Find notes that haven't been updated in 30+ days and may contain outdated info.
Flag cryptocurrency prices, deadlines, or time-sensitive claims that may be stale.

### Auto-dispatch (called automatically after another agent finishes)
When your task starts with "An agent just finished a task. Review its output...", this is an automatic post-task vault check. Do the following in order:
1. Read the agent's result summary from the task description.
2. Decide: does this output contain new knowledge, decisions, research, or data worth saving?
   - YES → write or update the most appropriate vault note. Use index.md to find the right folder.
   - NO → skip (don't create empty or duplicate notes).
3. If you wrote or updated a note, add cross-links to related notes you know about.
4. Run a quick vault inspection:
   - Check the last 5 log entries: `tail -20 "$BASE/log.md"`
   - Look for any orphaned notes created recently: files in the last 2 days with no inbound links
   - Verify the index.md has an entry for any new notes you just wrote
5. Log any changes you made to log.md.
6. Return a short summary: what you saved (or why you skipped), and any inspection findings.

## How to find all vault files
```bash
BASE="$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain"
find "$BASE" -name "*.md" -not -path "*/.obsidian/*" -not -path "*/.trash/*"
```

## How to check for links in a file
```bash
grep -l "\\[\\[NoteTitle\\]\\]" "$BASE"/**/*.md 2>/dev/null
```

## Writing rules
- Always maintain existing frontmatter (tags, etc.)
- Add links naturally in context — don't just dump them at the bottom
- Keep the vault's writing style consistent (see existing notes for tone)
- Write vault files via Bash (iCloud sync — no MCP tools)
- Log all changes to {str(VAULT)}/log.md:
  ```
  ## [YYYY-MM-DD] maintenance | Brief description of what was done
  - List of specific changes
  ```

## Output
Return a concise summary of:
- How many issues found
- What was fixed
- What was added
- Any remaining issues that need Taran's input
"""


def is_running() -> bool:
    return _running


def _load_lessons(max_chars: int = 1000) -> str:
    lessons = AGENTIC_DIR / "lessons.md"
    if not lessons.exists() or lessons.stat().st_size == 0:
        return ""
    return "\n\n## Past Lessons\n" + lessons.read_text(encoding="utf-8")[-max_chars:]


async def run_vault_curator_task(task_description: str, broadcast, send_telegram, sandbox_dir=None):
    global _running
    _running = True
    try:
        await broadcast({"type": "vault_curator_activity", "text": f"Starting: {task_description[:80]}"})

        from tools.llm import run_llm_command
        full_prompt = f"{CURATOR_CONTEXT}{_load_lessons()}\n\n## Task\n{task_description}"

        res = await run_llm_command(
            prompt=full_prompt,
            broadcast=broadcast,
            send_telegram=send_telegram,
            sandbox_dir=sandbox_dir,
            agent_name="vault_curator",
        )

        result = res.get("result", "Vault curation complete.")
        await broadcast({"type": "vault_curator_activity", "text": "Curation complete."})

        # Incrementally re-index any vault files changed during this curation run
        try:
            import asyncio as _asyncio
            from tools.rag import index_changed_files
            n_chunks = await _asyncio.to_thread(index_changed_files)
            if n_chunks:
                await broadcast({"type": "vault_curator_activity",
                                 "text": f"RAG: re-indexed changed files ({n_chunks} chunks updated)"})
        except Exception as rag_err:
            log.debug("RAG incremental index skipped: %s", rag_err)

        await send_telegram("🧠 Vault curation done. Check the log for changes.")
        return result

    except Exception as e:
        log.exception("Vault curator error")
        await broadcast({"type": "vault_curator_error", "text": str(e)})
        return f"Error: {e}"
    finally:
        _running = False
