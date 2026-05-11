# Agentic OS

Taran's personal autonomous AI agent system running locally on macOS.

Agentic OS is a multi-agent orchestration system designed to automate workflows, manage personal tasks, and provide a unified interface for AI interactions via Telegram and a web dashboard.

## 🚀 Features

- **Multi-Agent Orchestration**: Intelligent routing of tasks to specialized workflows (Career, Personal, Code Review).
- **Dual-LLM Runner**: Seamless fallback between Claude (Claude Code CLI) and Gemini (Gemini CLI) for maximum reliability and usage optimization.
- **Telegram Integration**: Full control over the system via a Telegram bot, including task execution and approval requests.
- **Interactive Dashboard**: Real-time monitoring of tasks, usage tracking, and manual control through a FastAPI-based web interface.
- **Autonomous Workflows**:
  - **Career Search**: Automated job searching and application tracking.
  - **Code Review**: Multi-agent review (Security, Efficiency, Logic) of git diffs or projects.
  - **Personal Tasks**: Management of daily tasks and reminders.
- **Obsidian Integration**: Automatically writes reports and logs to a local Obsidian vault for long-term memory.

## 🛠 Tech Stack

- **Backend**: Python, FastAPI, Uvicorn
- **Frontend**: HTML5, Vanilla CSS, WebSockets
- **LLMs**: Claude (Anthropic), Gemini (Google)
- **Tools**: Playwright (Web automation), Git, Telegram Bot API
- **OS**: Optimized for macOS (uses `say` for TTS)

## 📋 Prerequisites

- macOS (recommended for full feature support)
- Python 3.10+
- [Claude Code CLI](https://github.com/anthropics/claude-code) installed and authenticated.
- [Gemini CLI](https://github.com/google/gemini-cli) installed and authenticated.
- A Telegram Bot Token and Chat ID.

## ⚙️ Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/Taran132g/agentic-os.git
   cd agentic-os
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Configure environment variables:
   Copy `.env.example` to `.env` and fill in your details:
   ```bash
   cp .env.example .env
   # Edit .env with your tokens
   ```

## 🚀 Usage

Start the system:
```bash
python main.py
```

- **Dashboard**: Open `http://localhost:8000` in your browser.
- **Telegram**: Send `/task <your task>` to your bot.

## 🏗 Project Structure

- `main.py`: Entry point, runs FastAPI, Telegram bot, and Orchestrator.
- `orchestrator.py`: Core logic for task execution and routing.
- `agents/`: Specialized agent implementations.
- `tools/`: Shared utilities and LLM runner logic.
- `workflows/`: Specialized long-running task definitions.
- `dashboard/`: Frontend assets for the web interface.

## 🛡 Security

- Uses an approval system for sensitive actions (sending emails, pushing to GitHub, etc.).
- Local-first architecture; your data stays on your machine.

## 📝 License

MIT
