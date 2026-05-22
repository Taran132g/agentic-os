# Agentic OS

A personal autonomous AI agent system running locally on macOS. PAIS handles tasks end-to-end — research, job applications, personal organization — via a web dashboard or Telegram, with full computer use capabilities.

![Python](https://img.shields.io/badge/Python-3.10+-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-green) ![License](https://img.shields.io/badge/License-MIT-yellow)

---

## How It Works

1. Submit a task via Telegram (`/task ...`) or the web dashboard
2. PAIS routes it to the right agent: **Career**, **Personal**, or **General**
3. The agent runs autonomously using Claude Code CLI
4. Results are logged to your Obsidian vault; you're notified on Telegram
5. Sensitive actions (sending emails, pushing to GitHub) require your explicit approval

---

## Features

### Multi-Agent Architecture
Four independent async workers run in parallel — career, personal, general, and a Telegram intake worker. Workers are freed immediately after a task completes; verification and self-correction run asynchronously so the queue never stalls.

### Claude-Only LLM Runner
Runs Claude Code CLI exclusively. On usage limit errors, PAIS waits 30 seconds and retries automatically — no Gemini dependency, no manual intervention needed.

### Self-Correction Loop
When a task doesn't complete correctly, click "Didn't complete correctly" and type what went wrong. PAIS reads its own source code, identifies the root cause, patches the file, writes a lesson to `lessons.md`, hot-reloads the module, and re-queues the original task — all without a restart.

### Persistent Learning (`lessons.md`)
An append-only self-correction log stored at the project root. Every agent reads the most recent lessons before executing a task, so PAIS improves from past mistakes across sessions.

### Computer Use
PAIS can control your Mac desktop: take screenshots, click, type, scroll, drag, run AppleScript, OCR the screen, and open apps. Useful for automating GUI-only workflows.

### Career Workflow
Searches for jobs matching keywords, tailors resume bullets per job description, and uses Playwright to open application forms and pre-fill them — stopping before submit so you review before anything is sent. Search results are cached to disk so the Career Ops page restores them on page load.

### Personal Tasks Workflow
Handles research, planning, and personal organization. Analyzes the task, creates a structured plan, and writes it to your Obsidian vault. Includes the "Didn't complete correctly" self-correction button.

### Task Log
Every completed task is saved to the vault with a `YYYY-MM-DD HH-MM` filename and a `## Requested:` field. The dashboard history view is sorted by modification time with full `YYYY-MM-DD HH:MM` timestamps so you can see exactly when each task ran.

### Telegram Bot
- `/task <description>` — queue a task
- `/status` — check queue and pending approvals
- Inline buttons for routing (Career / Personal / General) and Approve/Deny on sensitive actions

### Web Dashboard
Real-time FastAPI dashboard with WebSocket push. Shows live task output, task history from the vault, system logs, and manual controls. Includes a self-correction feedback panel and a dedicated Career Ops page.

### Obsidian Integration
All completed tasks, career search results, and personal plans are written as Markdown notes to a local Obsidian vault for long-term memory.

### Approval Gate
Any action with external side effects (sending email, pushing code, posting online) is paused and a request is sent to both Telegram and the dashboard. Nothing irreversible happens without your explicit approval.

### Always-On Mac
PAIS runs via a macOS LaunchAgent wrapped with `caffeinate -i -s` so the machine never sleeps while the agent is active.

---

## Task Log Audit (May 2026)

After the May 11 build session, a 7-task end-to-end audit was run to stress-test PAIS after the worker stall fix and self-correction loop were introduced. Each task was queued live and verified:

| Task | Result |
|------|--------|
| Always-on Mac setup | ✅ Done — LaunchAgent + caffeinate configured |
| DigitalOcean hosting research | ✅ Done — s-2vcpu-4gb @ $24/mo documented in vault |
| Career search (SWE intern 2027) | ✅ Done — 4 jobs found, resumes tailored |
| IBM recruiter email draft | ✅ Done — full draft written to vault |
| UTR Spring-Ford tennis roster | ✅ Done — 77 players, UTR ratings extracted |
| Birthday video iMovie template | ⚠️ Partial — iMovie opened, blocked by Chrome/Teams at z-index 999 |
| DigitalOcean computer use plan | ✅ Done — plan documented in vault |

**Key bugs found and fixed during the audit:**
- **Worker stall** — workers were blocking on verification; fixed by moving verification async into `_pending_verifications`
- **Gemini removal** — Gemini CLI was causing intermittent failures; removed entirely, Claude-only with 30s retry
- **Task log ordering** — history was unordered; now sorted by `mtime` with `HH:MM` timestamps
- **Career jobs disappearing** — page reload wiped search results; fixed by persisting to `career_jobs_cache.json`

---

## Tech Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.10+, FastAPI, Uvicorn, asyncio |
| Frontend | HTML5, Vanilla CSS/JS, WebSockets |
| LLM | Claude Code CLI (Anthropic) |
| Browser automation | Playwright |
| Desktop control | PyAutoGUI, screencapture, AppleScript |
| Notifications | Telegram Bot API (python-telegram-bot) |
| Storage | Obsidian vault (local Markdown files) |

---

## Prerequisites

- macOS (required for computer use features)
- Python 3.10+
- [Claude Code CLI](https://github.com/anthropics/claude-code) — installed and authenticated
- A Telegram bot token and your chat ID

---

## Installation

```bash
git clone https://github.com/Taran132g/agentic-os.git
cd agentic-os
pip install -r requirements.txt
playwright install chromium
```

Create a `.env` file in the project root:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
GITHUB_TOKEN=your_github_token_here   # optional
VAULT_PATH=/path/to/your/obsidian/vault  # optional, has a default
DASHBOARD_PORT=8000                    # optional
```

---

## Usage

```bash
python main.py
```

- **Dashboard:** `http://localhost:8000`
- **Telegram:** Send `/task research the best Python async patterns` to your bot

To reload the LaunchAgent after config changes:
```bash
launchctl unload ~/Library/LaunchAgents/com.taran.pais.plist
launchctl load ~/Library/LaunchAgents/com.taran.pais.plist
```

---

## Project Structure

```
agentic_os/
├── main.py                     # Entry point: FastAPI server + async workers + WebSocket
├── orchestrator.py             # General task runner (Claude Code CLI + lessons injection)
├── career_workflow.py          # Job search → resume tailoring → form pre-fill → jobs cache
├── personal_tasks_workflow.py  # Personal task planning and research + lessons injection
├── telegram_bot.py             # Bot commands, routing buttons, approval callbacks
├── config.py                   # Env var loading
├── lessons.md                  # Append-only self-correction log (read by all agents)
├── deploy.sh                   # Deployment helper for DigitalOcean
├── tools/
│   ├── llm.py                  # Claude Code CLI runner with 30s usage-limit retry
│   ├── computer.py             # Mac desktop control (screenshot, click, OCR, etc.)
│   ├── approval.py             # Approval gate for sensitive actions
│   ├── logger.py               # Vault task logger (YYYY-MM-DD HH-MM filenames)
│   ├── vault.py                # Obsidian vault read/write helpers
│   ├── web.py                  # Web search and fetch utilities
│   ├── github_tools.py         # GitHub API helpers
│   └── playwright_apply.py     # Browser automation for job applications
├── dashboard/
│   ├── index.html              # Main dashboard
│   ├── career.html             # Career workflow UI (cached jobs, Gemini button removed)
│   └── personal.html           # Personal tasks UI (self-correction feedback panel)
└── agents/                     # Agent class definitions
```

---

## Security

- Single-user: the Telegram bot rejects messages from any chat ID other than yours
- Approval gate blocks all external side-effect actions until you explicitly approve
- Local-first: all data stays on your machine; no data is sent to third-party services beyond the LLM APIs

---

## License

MIT
