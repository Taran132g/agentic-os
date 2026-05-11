# Agentic OS

A personal autonomous AI agent system running locally on macOS. JARVIS handles tasks end-to-end — research, job applications, personal organization — via a web dashboard or Telegram, with full computer use capabilities.

![Python](https://img.shields.io/badge/Python-3.10+-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-green) ![License](https://img.shields.io/badge/License-MIT-yellow)

---

## How It Works

1. Submit a task via Telegram (`/task ...`) or the web dashboard
2. JARVIS routes it to the right agent: **Career**, **Personal**, or **General**
3. The agent runs autonomously using Claude (with Gemini fallback)
4. Results are logged to your Obsidian vault; you're notified on Telegram
5. Sensitive actions (sending emails, pushing to GitHub) require your explicit approval

---

## Features

### Multi-Agent Architecture
Four independent async workers run in parallel — career, personal, general, and a Telegram intake worker. Submitting a new task never blocks an in-progress one.

### Dual-LLM Runner
Runs Claude Code CLI by default. Automatically falls back to Gemini CLI on usage limits or failures. Provider can also be set manually via the dashboard.

### Computer Use
JARVIS can control your Mac desktop: take screenshots, click, type, scroll, drag, run AppleScript, OCR the screen, and open apps. Useful for automating GUI-only workflows.

### Career Workflow
Searches for jobs matching keywords, tailors resume bullets per job description, and uses Playwright to open application forms and pre-fill them — stopping before submit so you review before anything is sent.

### Personal Tasks Workflow
Handles research, planning, and personal organization. Analyzes the task, creates a structured plan, and writes it to your Obsidian vault.

### Telegram Bot
- `/task <description>` — queue a task
- `/status` — check queue and pending approvals
- Inline buttons for routing (Career / Personal / General) and Approve/Deny on sensitive actions

### Web Dashboard
Real-time FastAPI dashboard with WebSocket push. Shows live task output, usage stats (tokens, tasks completed), task history from the vault, system logs, and manual controls.

### Obsidian Integration
All completed tasks, career search results, and personal plans are written as Markdown notes to a local Obsidian vault for long-term memory.

### Approval Gate
Any action with external side effects (sending email, pushing code, posting online) is paused and a request is sent to both Telegram and the dashboard. Nothing irreversible happens without your explicit approval.

---

## Tech Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.10+, FastAPI, Uvicorn, asyncio |
| Frontend | HTML5, Vanilla CSS/JS, WebSockets |
| LLMs | Claude Code CLI (Anthropic), Gemini CLI (Google) |
| Browser automation | Playwright |
| Desktop control | PyAutoGUI, screencapture, AppleScript |
| Notifications | Telegram Bot API (python-telegram-bot) |
| Storage | Obsidian vault (local Markdown files) |

---

## Prerequisites

- macOS (required for computer use features)
- Python 3.10+
- [Claude Code CLI](https://github.com/anthropics/claude-code) — installed and authenticated
- [Gemini CLI](https://github.com/google/gemini-cli) — installed and authenticated (optional, used as fallback)
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

---

## Project Structure

```
agentic_os/
├── main.py                     # Entry point: FastAPI server + async workers + WebSocket
├── orchestrator.py             # General task runner (Claude Code CLI)
├── career_workflow.py          # Job search → resume tailoring → form pre-fill
├── personal_tasks_workflow.py  # Personal task planning and research
├── telegram_bot.py             # Bot commands, routing buttons, approval callbacks
├── config.py                   # Env var loading
├── tools/
│   ├── llm.py                  # Claude / Gemini CLI runner with auto-fallback
│   ├── computer.py             # Mac desktop control (screenshot, click, OCR, etc.)
│   ├── approval.py             # Approval gate for sensitive actions
│   ├── logger.py               # Vault task logger
│   ├── vault.py                # Obsidian vault read/write helpers
│   ├── web.py                  # Web search and fetch utilities
│   ├── github_tools.py         # GitHub API helpers
│   └── playwright_apply.py     # Browser automation for job applications
├── dashboard/
│   ├── index.html              # Main dashboard
│   ├── career.html             # Career workflow UI
│   └── personal.html           # Personal tasks UI
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
