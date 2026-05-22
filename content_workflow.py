"""
Content Agent — short-form faceless video pipeline.
Writes script via Claude, then calls content_pipeline.py directly to produce the video.
"""

import asyncio
import logging
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)
_running = False

AGENTIC_DIR = Path(__file__).parent
VAULT = Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain"

SCRIPT_CONTEXT = """You are PAIS writing a short-form script for Taran's faceless content pipeline.

## Content style
- Niche: introspective philosophy, psychology, self-awareness
- Format: TikTok/Reels (1080x1920 vertical), 45-90 seconds
- Voice: calm, contemplative, authoritative
- Tone: makes the viewer stop scrolling and think

## Script format
Each line = one spoken sentence. Optional | keyword hint for B-roll search.

Example:
```
Most people never question why they do what they do. | crowd walking
They just follow the path laid out for them. | empty road
School. Job. Retire. | calendar desk clock
```

## Topic selection
If the task says "topic: [X]", use that.
Otherwise choose from:
- Why people stay in comfort zones
- The illusion of productivity
- Social media and identity
- Why most people never change
- The psychology of decision-making
- What successful people do differently

## Script rules
- 8-15 lines total (45-90 seconds at speaking pace)
- Hook in line 1 — make them stop scrolling immediately
- Build tension/insight through the middle
- End with a thought-provoking statement, NOT a call to action
- Short sentences, simple vocabulary
- Avoid: "in a world where…", "have you ever wondered…"
- Don't preach — present observations, let viewer conclude

## Output format
Return ONLY the raw script lines (no headers, no JSON, no explanation).
Each line: spoken text | broll keyword hint
If no B-roll hint needed, omit the | entirely.
"""

DEFAULT_TOPICS = [
    "Why people stay in comfort zones",
    "The illusion of productivity",
    "Social media and identity",
    "Why most people never change",
    "The psychology of decision-making",
]


def is_running() -> bool:
    return _running


def _load_lessons(max_chars: int = 1000) -> str:
    lessons = AGENTIC_DIR / "lessons.md"
    if not lessons.exists() or lessons.stat().st_size == 0:
        return ""
    return "\n\n## Past Lessons\n" + lessons.read_text(encoding="utf-8")[-max_chars:]


def _save_script_to_vault(script_text: str, topic: str) -> Path:
    """Save generated script to vault as fallback record."""
    scripts_dir = VAULT / "Content Pipeline" / "Scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    slug = re.sub(r"[^\w]", "-", topic.lower())[:40]
    path = scripts_dir / f"{date_str}-{slug}.md"
    content = f"""---
tags:
  - content
  - script
---

# Script — {topic}

**Date:** {date_str}

```
{script_text}
```
"""
    path.write_text(content, encoding="utf-8")
    return path


async def _generate_script(task_description: str, broadcast) -> str:
    """Use Claude to write the video script. Returns raw script text."""
    from tools.llm import run_llm_command

    full_prompt = (
        f"{SCRIPT_CONTEXT}{_load_lessons()}\n\n"
        f"## Task\n{task_description}\n\n"
        "Now write the script:"
    )
    res = await run_llm_command(
        prompt=full_prompt,
        broadcast=broadcast,
        allowed_tools="",  # script writing needs no tools
        agent_name="content",
    )
    return res.get("result", "").strip()


async def _run_pipeline(script_path: Path, broadcast) -> dict:
    """Run content_pipeline.py as a subprocess and return {ok, output, video_path}."""
    pipeline = AGENTIC_DIR / "content_pipeline.py"
    python = sys.executable

    await broadcast({"type": "content_activity", "text": "Running pipeline: voiceover → B-roll → ffmpeg…"})

    proc = await asyncio.create_subprocess_exec(
        python, str(pipeline), str(script_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(AGENTIC_DIR),
    )

    output_lines: list[str] = []
    assert proc.stdout
    async for line in proc.stdout:
        decoded = line.decode("utf-8", errors="replace").rstrip()
        output_lines.append(decoded)
        await broadcast({"type": "content_activity", "text": decoded})

    await proc.wait()
    output = "\n".join(output_lines)

    # Try to find the output video path from the pipeline output
    video_path = None
    for line in reversed(output_lines):
        if line.startswith("Done!") and ".mp4" in line:
            match = re.search(r"(/[^\s]+\.mp4)", line)
            if match:
                video_path = match.group(1)
            break

    return {"ok": proc.returncode == 0, "output": output, "video_path": video_path}


async def run_content_task(task_description: str, broadcast, send_telegram, sandbox_dir=None):
    global _running
    _running = True
    try:
        await broadcast({"type": "content_activity", "text": f"Starting: {task_description[:80]}"})

        # Step 1: Generate script via Claude
        await broadcast({"type": "content_activity", "text": "Writing script…"})
        script_text = await _generate_script(task_description, broadcast)

        if not script_text:
            return "Content agent: script generation returned empty result."

        # Extract topic from task or use first script line as title
        topic_match = re.search(r"topic[:\s]+(.+?)(?:\n|$)", task_description, re.I)
        topic = topic_match.group(1).strip() if topic_match else script_text.split("\n")[0][:60]

        await broadcast({"type": "content_activity", "text": f"Script written ({len(script_text.splitlines())} lines). Saving…"})

        # Step 2: Save script to temp file for pipeline
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", prefix="content_script_",
            delete=False, encoding="utf-8"
        ) as f:
            f.write(script_text)
            script_path = Path(f.name)

        # Also save to vault as a record
        vault_script_path = _save_script_to_vault(script_text, topic)
        await broadcast({"type": "content_activity", "text": f"Script saved to vault: {vault_script_path.name}"})

        # Step 3: Run the pipeline
        pipeline_result = await _run_pipeline(script_path, broadcast)

        # Clean up temp file
        try:
            script_path.unlink()
        except Exception:
            pass

        if pipeline_result["ok"] and pipeline_result["video_path"]:
            video_path = pipeline_result["video_path"]
            result = f"Video ready: {video_path}\nScript ({len(script_text.splitlines())} lines) also saved to vault."
            await broadcast({"type": "content_activity", "text": f"Done! Video at {video_path}"})
            await send_telegram(f"Content pipeline done — video at: {video_path}")
        else:
            # Pipeline failed — script is saved to vault
            result = (
                f"Pipeline failed (check API keys / ffmpeg). "
                f"Script saved to vault at: {vault_script_path}\n\n"
                f"To run manually:\n  cd ~/agentic_os && python3 content_pipeline.py /path/to/script.txt"
            )
            await broadcast({"type": "content_activity", "text": "Pipeline failed — script saved to vault."})
            await send_telegram(f"Content pipeline failed — script saved to vault. Run manually if needed.")

        return result

    except Exception as e:
        log.exception("Content task error")
        await broadcast({"type": "content_error", "text": str(e)})
        return f"Error: {e}"
    finally:
        _running = False
