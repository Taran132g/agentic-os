#!/usr/bin/env python3
"""
pais_bridge.py — local LLM bridge (2026-06-10).

Runs `claude -p` (Taran's Claude subscription — no API key, no credits) and
serves it over HTTP to the PAIS web backend on Oracle, which reaches it through
a reverse SSH tunnel (Oracle's localhost:8787 → this Mac's localhost:8787).

The web backend POSTs a composed prompt here; we run it through the Claude CLI
and return the text. Token-gated; binds to localhost only (the tunnel is the
only ingress). If this Mac is asleep/offline the tunnel drops and the backend
gets a clean "AI offline" — by design.

Env:
    BRIDGE_TOKEN   required shared secret (matches the Oracle backend)
    BRIDGE_MODEL   default model (default: claude-sonnet-4-6)
    BRIDGE_PORT    default 8787
"""

import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ.get("BRIDGE_TOKEN", "")
DEFAULT_MODEL = os.environ.get("BRIDGE_MODEL", "claude-sonnet-4-6")
PORT = int(os.environ.get("BRIDGE_PORT", "8787"))
MAX_PROMPT = 200_000  # safety cap


def run_claude(prompt: str, model: str) -> str:
    """One subscription-billed completion via the Claude CLI (prompt on stdin)."""
    proc = subprocess.run(
        ["claude", "-p", "--model", model],
        input=prompt, capture_output=True, text=True, timeout=240,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "claude failed").strip()[:500])
    return (proc.stdout or "").strip()


def _run_claude_tools(prompt: str, model: str, tools: str) -> str:
    """Agentic run with tools (e.g. WebSearch,WebFetch). Longer timeout."""
    proc = subprocess.run(
        ["claude", "-p", prompt, "--model", model, "--allowedTools", tools,
         "--dangerously-skip-permissions"],
        capture_output=True, text=True, timeout=700,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "claude failed").strip()[:500])
    return (proc.stdout or "").strip()


# Relevance threshold for vault chunks (cosine distance; lower = closer).
SEARCH_MAX_DISTANCE = float(os.environ.get("BRIDGE_SEARCH_MAX_DISTANCE", "0.55"))

# Local n8n webhook that runs fill_scouted.py (opens browser windows on this
# Mac and fills scouted applications — the user reviews + submits each one).
N8N_APPLY_WEBHOOK = os.environ.get("N8N_APPLY_WEBHOOK", "http://localhost:5678/webhook/apply")
AGENTIC_DIR = os.path.dirname(os.path.abspath(__file__))
SCOUT_JOBS = os.path.join(AGENTIC_DIR, "scout_jobs.json")

# Real execution: each web agent runs the SAME script as the n8n morning stack
# (morning_stack.sh), so a website run is identical to the scheduled routine.
# Telegram pings, Gmail drafts, scout_jobs.json, git pushes — all real.
SCRIPT_AGENTS = {
    "briefing": {"cmd": "python3 vault_digest.py"},
    "email":    {"cmd": "python3 email_triage.py"},
    "career":   {"cmd": "python3 job_scout.py"},
    "outreach": {"cmd": "python3 piontrix_scout.py; "
                        "OUTREACH_GMAIL_DRAFT=1 python3 piontrix_outreach.py --batch; "
                        "OUTREACH_GMAIL_DRAFT=1 python3 brainscan_outreach.py"},
    "linkedin": {"cmd": "OUTREACH_LIMIT=1 python3 linkedin_internship.py"},
    "code":     {"cmd": "python3 tools/repo_sync.py"},
}
SCRIPT_TIMEOUT = int(os.environ.get("BRIDGE_SCRIPT_TIMEOUT", "540"))


def run_script_agent(agent: str, persona: str = "", fields: dict | None = None) -> str:
    """Execute the agent's real n8n-equivalent script, then turn the actual run
    log into a descriptive feed report (raw log fallback if claude fails).
    The user's persona/fields are exported so the script's own claude prompts
    honor them (tools/persona.py)."""
    spec = SCRIPT_AGENTS[agent]
    env = {**os.environ, "ANTHROPIC_MODEL": "claude-sonnet-4-6"}  # cost pin, same as morning_stack
    if persona:
        env["PAIS_PERSONA"] = persona
    if fields:
        env["PAIS_FIELDS"] = json.dumps(fields, ensure_ascii=False)
    proc = subprocess.run(["/bin/sh", "-c", spec["cmd"]], cwd=AGENTIC_DIR, env=env,
                          capture_output=True, text=True, timeout=SCRIPT_TIMEOUT)
    raw = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if err:
        raw += "\n[stderr]\n" + err
    raw = raw[-6000:].strip() or "(script produced no output)"
    status = "completed" if proc.returncode == 0 else f"exited with code {proc.returncode}"

    voice = (f"\nWrite the report in line with the user's configured persona for this "
             f"agent: {persona}\n" if persona else "")
    prompt = (
        f"The user's {agent} agent just ACTUALLY ran on their computer — the exact same "
        f"script the scheduled n8n morning routine runs. It {status}. Below is the real "
        f"run log.\n\nWrite the update to post to the user's feed. Be AS DESCRIPTIVE AS "
        f"POSSIBLE and STRICTLY FACTUAL to the log — never invent items not in it. "
        f"Include: everything the run actually did (every email/lead/job/repo/item by "
        f"name, with numbers and links from the log), outcomes and failures, and anything "
        f"that needs the user's attention or a decision. Plain text with short section "
        f"headers and bullets (no markdown #).\n{voice}\nRUN LOG:\n{raw}"
    )
    try:
        report = run_claude(prompt, DEFAULT_MODEL)
        if report:
            return report
    except Exception:
        pass
    return f"Run {status}. Raw output:\n\n{raw}"


def trigger_job_apply() -> str:
    """Fire the n8n apply webhook (fire-and-forget) and return a descriptive
    feed message. The fill runs on THIS computer and needs the user's attention:
    every window is left open for review, résumé upload, and manual submit."""
    import threading
    import urllib.request

    jobs = []
    try:
        with open(SCOUT_JOBS) as f:
            data = json.load(f)
        jobs = data if isinstance(data, list) else data.get("jobs", [])
    except Exception:
        jobs = []

    def _fire():
        req = urllib.request.Request(
            N8N_APPLY_WEBHOOK, data=b"{}", method="POST",
            headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=1800).read()
        except Exception:
            pass

    threading.Thread(target=_fire, daemon=True).start()

    if jobs:
        lines = []
        for i, j in enumerate(jobs, 1):
            company = j.get("company", "?")
            role = j.get("role") or j.get("title") or ""
            score = j.get("match_score")
            bits = [f"{i}. {company}"]
            if role:
                bits.append(f"— {role}")
            if score:
                bits.append(f"(match {score})")
            url = j.get("url", "")
            lines.append(" ".join(bits) + (f"\n   {url}" if url else ""))
        queue = "\n".join(lines)
        head = f"🖥️ Job Apply engaged on your computer — {len(jobs)} scouted application(s) queued:\n\n{queue}"
    else:
        head = ("🖥️ Job Apply engaged on your computer — the fill pipeline started, "
                "but the scout queue (scout_jobs.json) looks empty. Run the Career "
                "agent first to scout fresh roles, then run me again.")
    return (
        f"{head}\n\n"
        "How this works: Chrome windows open ONE BY ONE on your Mac; each gets your "
        "tailored application brief pasted into the Gemini panel and 'Start task' "
        "clicked, then the next job begins. Every window is LEFT OPEN on purpose.\n\n"
        "⚠️ NEEDS YOUR ATTENTION — for each window you must: (1) review the filled "
        "form for accuracy, (2) attach your résumé (uploads can't be automated), "
        "(3) click Submit yourself. Nothing is submitted without you.\n\n"
        "A Telegram summary with per-job outcomes lands when the fill pass finishes."
    )


# Keep the brain fresh: re-embed changed vault notes (incremental, hash-based)
# at most every REINDEX_MAX_AGE seconds, in the background so requests never wait.
REINDEX_MAX_AGE = int(os.environ.get("BRIDGE_REINDEX_MAX_AGE", "600"))
_reindex_lock = threading.Lock()
_last_reindex = 0.0


def refresh_brain_async() -> None:
    global _last_reindex
    with _reindex_lock:
        if time.time() - _last_reindex < REINDEX_MAX_AGE:
            return
        _last_reindex = time.time()

    def _job():
        try:
            from tools import rag
            rag.index_changed_files()
            # Touch the meta file even when nothing changed — its mtime is the
            # "brain last verified fresh" timestamp shown in the product UI.
            os.utime(str(rag.META_FILE), None)
        except Exception:
            pass

    threading.Thread(target=_job, daemon=True).start()


def search_brain(query: str, top_k: int) -> list:
    """Semantic search over the local vault (Ollama embeddings + ChromaDB) — the
    user's own digital brain, indexed by the local PAIS RAG. No cloud, no credits."""
    from tools import rag  # lazy: needs chromadb + ollama
    col = rag._get_collection()
    if col is None or not rag._ollama_alive() or col.count() == 0:
        return []
    qe = rag._embed([query])
    if not qe:
        return []
    res = col.query(query_embeddings=qe, n_results=min(top_k, col.count()),
                    include=["documents", "metadatas", "distances"])
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    dists = res.get("distances", [[]])[0]
    out = []
    for d, m, dist in zip(docs, metas, dists):
        if dist <= SEARCH_MAX_DISTANCE:
            src = m.get("source", "note")
            out.append({"text": d, "title": src, "file_path": src})
    return out


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "model": DEFAULT_MODEL})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path not in ("/llm", "/search", "/stats", "/run-agent"):
            return self._send(404, {"error": "not found"})
        if not TOKEN or self.headers.get("Authorization", "") != "Bearer " + TOKEN:
            return self._send(401, {"error": "unauthorized"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, {"error": "bad json"})

        if self.path == "/search":
            query = (data.get("query") or "").strip()
            if not query:
                return self._send(400, {"error": "query required"})
            refresh_brain_async()   # pick up new/edited vault notes for next queries
            try:
                self._send(200, {"chunks": search_brain(query, int(data.get("top_k", 12)))})
            except Exception as e:
                self._send(502, {"error": str(e)[:300]})
            return

        if self.path == "/run-agent":
            # Run an agent's work: brain context + persona (+ optional WebSearch)
            # → a detailed, descriptive update to post to the user's feed.
            agent = (data.get("agent") or "").strip()
            persona = (data.get("persona") or "").strip()
            fields = data.get("fields") or {}
            query = (data.get("query") or persona or agent).strip()
            use_web = bool(data.get("web"))

            # Job Apply runs ON this computer (n8n → fill_scouted.py browser
            # fills) and needs the user's attention — no LLM call needed.
            if agent == "apply":
                try:
                    return self._send(200, {"text": trigger_job_apply()})
                except Exception as e:
                    return self._send(502, {"error": str(e)[:300]})

            # Script agents run the REAL n8n-equivalent job on this computer —
            # identical to the scheduled morning routine.
            if agent in SCRIPT_AGENTS and not data.get("simulate"):
                refresh_brain_async()
                try:
                    return self._send(200, {"text": run_script_agent(agent, persona, fields)})
                except subprocess.TimeoutExpired:
                    return self._send(504, {"error": "agent script timed out"})
                except Exception as e:
                    return self._send(502, {"error": str(e)[:300]})

            refresh_brain_async()
            try:
                ctx = ""
                try:
                    chunks = search_brain(query, 10)
                    ctx = "\n\n".join((c.get("text") or "")[:500] for c in chunks)[:5000]
                except Exception:
                    ctx = ""
                fld = "; ".join(f"{k}: {v}" for k, v in fields.items() if v)
                prompt = (
                    f"{persona or ('You are the user’s ' + agent + ' agent.')}\n\n"
                    f"Their relevant notes:\n{ctx or '(none found)'}\n\n"
                    f"Your settings: {fld or '(none)'}\n\n"
                    f"Do your job now for the user and write the update to post to their feed. "
                    f"Be AS DESCRIPTIVE AS POSSIBLE — a complete, self-contained report, not a "
                    f"teaser. Include: everything you found or produced (list every item, don't "
                    f"summarize away detail), the specific names, companies, dates, numbers, and "
                    f"links involved, WHY each item matters or was chosen (your reasoning), what "
                    f"you'd do next, and anything that needs the user's attention or decision. "
                    f"Name real things from the notes. Structure it with short section headers "
                    f"and bullet lists in plain text (no markdown #), so it reads well in a feed. "
                    f"Length: as long as the substance requires — never pad, never truncate."
                )
                model = data.get("model") or DEFAULT_MODEL
                tools = "WebSearch,WebFetch" if use_web else None
                self._send(200, {"text": run_claude(prompt, model) if not tools else _run_claude_tools(prompt, model, tools)})
            except subprocess.TimeoutExpired:
                self._send(504, {"error": "agent timed out"})
            except Exception as e:
                self._send(502, {"error": str(e)[:300]})
            return

        if self.path == "/stats":
            try:
                from tools import rag
                s = rag.get_stats()
                updated = None
                try:
                    updated = int(os.path.getmtime(str(rag.META_FILE)) * 1000)
                except Exception:
                    pass
                self._send(200, {"ready": s.get("status") == "ready",
                                 "notes": s.get("files_indexed", 0),
                                 "chunks": s.get("chunks", 0),
                                 "updated": updated})
            except Exception as e:
                self._send(502, {"error": str(e)[:200]})
            return

        prompt = (data.get("prompt") or "").strip()
        if not prompt:
            return self._send(400, {"error": "prompt required"})
        if len(prompt) > MAX_PROMPT:
            return self._send(413, {"error": "prompt too large"})
        model = data.get("model") or DEFAULT_MODEL
        try:
            self._send(200, {"text": run_claude(prompt, model)})
        except subprocess.TimeoutExpired:
            self._send(504, {"error": "claude timed out"})
        except Exception as e:
            self._send(502, {"error": str(e)[:300]})

    def log_message(self, *a):
        pass  # quiet


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("BRIDGE_TOKEN not set")
    print(f"pais_bridge on 127.0.0.1:{PORT} (model {DEFAULT_MODEL})")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
