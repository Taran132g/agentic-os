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
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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

# Local PAIS dashboard server (main.py) — hosts the live-trading endpoints.
PAIS_LOCAL_URL = os.environ.get("PAIS_LOCAL_URL",
                                f"http://localhost:{os.environ.get('DASHBOARD_PORT', '8000')}")


def _local_pais(path: str, method: str = "POST", payload: dict | None = None,
                timeout: int = 30) -> dict:
    """Call the local PAIS server (main.py). Raises on transport errors."""
    import urllib.request
    body = json.dumps(payload or {}).encode() if method == "POST" else None
    req = urllib.request.Request(
        PAIS_LOCAL_URL + path, data=body, method=method,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())

# Local n8n webhook that runs fill_scouted.py (opens browser windows on this
# Mac and fills scouted applications — the user reviews + submits each one).
N8N_APPLY_WEBHOOK = os.environ.get("N8N_APPLY_WEBHOOK", "http://localhost:5678/webhook/apply")
AGENTIC_DIR = os.path.dirname(os.path.abspath(__file__))
SCOUT_JOBS = os.path.join(AGENTIC_DIR, "scout_jobs.json")

# Shared job pipeline (the vault Job Pipeline.md note = single source of truth for
# scouting, the apply queue, and the Control Room tracker) + the MERGED Jobs runner.
# Reusing pais-runtime's run_jobs means an on-demand "Run" is byte-identical to the
# scheduled morning routine — no second fill implementation to drift.
import importlib.util
import sys
try:
    from tools import job_sheet  # type: ignore
except Exception:
    job_sheet = None
try:
    from tools import linkedin_sheet  # type: ignore
except Exception:
    linkedin_sheet = None
try:
    from tools import clip_sheet  # type: ignore
except Exception:
    clip_sheet = None
from tools import icloud_read  # iCloud-resilient vault read (shared with the sheets)
# Load pais-runtime/agents.py under a UNIQUE name — agentic_os already has an
# `agents/` package, so `import agents` would resolve to the wrong module.
_PAIS_RUNTIME = os.path.expanduser("~/pais-runtime")
pais_agents = None
try:
    _spec = importlib.util.spec_from_file_location(
        "pais_runtime_agents", os.path.join(_PAIS_RUNTIME, "agents.py"))
    if _spec and _spec.loader:
        pais_agents = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(pais_agents)
except Exception:
    pais_agents = None


def _telegram_reviewer(text: str) -> None:
    """Mirror the reviewer's audit to Telegram. The reviewer runs BACKEND-side
    (Oracle _execute_run → this bridge's /run-agent), so since the morning
    routine was unscheduled (2026-06-29) nothing else ever Telegrams its report
    — the audit only reached the web feed. Reuses pais-runtime's chunked sender
    (creds from agentic_os/.env, never hardcoded). Best-effort: never raises."""
    try:
        if _PAIS_RUNTIME not in sys.path:      # runtime.py does `from client import …`
            sys.path.insert(0, _PAIS_RUNTIME)
        spec = importlib.util.spec_from_file_location(
            "pais_runtime_runtime", os.path.join(_PAIS_RUNTIME, "runtime.py"))
        rt = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rt)
        rt._tg_agent_full("reviewer", text)
    except Exception:
        pass


# Real execution: each web agent runs the SAME script as the n8n morning stack
# (morning_stack.sh), so a website run is identical to the scheduled routine.
# Telegram pings, Gmail drafts, scout_jobs.json, git pushes — all real.
SCRIPT_AGENTS = {
    "briefing": {"cmd": "python3 vault_digest.py"},
    "email":    {"cmd": "python3 email_triage.py"},
    "career":   {"cmd": "python3 job_scout.py"},
    "outreach": {"cmd": "python3 piontrix_scout.py; "
                        "OUTREACH_GMAIL_DRAFT=1 python3 piontrix_outreach.py --batch; python3 backfill_phones.py"},
    "sales":    {"cmd": "python3 sales_agent.py"},
    "linkedin": {"cmd": "python3 linkedin_pais.py"},
    "code":     {"cmd": "python3 tools/repo_sync.py"},
}
# clips runs DETACHED (clips_worker.py) — a daily run can exceed SCRIPT_TIMEOUT.
SCRIPT_TIMEOUT = int(os.environ.get("BRIDGE_SCRIPT_TIMEOUT", "540"))
RUNNING_PROCS = {}            # agent → Popen (for /kill)
KILLED = set()
_PROC_LOCK = threading.Lock()
LAST_RUNS = {}                # agent → {"log", "report", "ts"} — context for /agent-chat


def spawn_clips_worker() -> str:
    """Detached daily clips run (clip_pipeline.py daily can take 5-15 min)."""
    worker = Path(os.path.expanduser("~/pais-runtime")) / "clips_worker.py"
    log = open("/tmp/pais_clips_worker.log", "a")
    subprocess.Popen([sys.executable, str(worker)], cwd=AGENTIC_DIR,
                     stdout=log, stderr=log, start_new_session=True)
    return ("🎬 Generating today's clips now — checking your creators for new "
            "videos, cutting the best moments, and captioning them. This runs in "
            "the background (5-15 min); the finished queue will land on your feed "
            "and in the Clip queue below. You can keep using the app meanwhile.")


GROWTH_TAPES = os.path.join(AGENTIC_DIR, "growth_tapes.json")

def _growth_registry() -> dict:
    """Growth-niche tape registry (tapes rendered by content_pipeline.py).
    Tapes surface in the clips queue but live outside the vault clip sheet."""
    try:
        with open(GROWTH_TAPES) as f:
            return json.load(f)
    except Exception:
        return {"tapes": []}


def _growth_tape(clip_id: str):
    return next((t for t in _growth_registry().get("tapes", [])
                 if t.get("id") == clip_id), None)


def _growth_mark(clip_id: str, status: str, post: str | None = None) -> bool:
    reg = _growth_registry()
    hit = False
    for t in reg.get("tapes", []):
        if t.get("id") == clip_id:
            t["status"] = status
            if post:
                t["posted"] = post
            hit = True
    if hit:
        with open(GROWTH_TAPES, "w") as f:
            json.dump(reg, f, indent=2, ensure_ascii=False)
    return hit


GROWTH_BROLL_GAMES = (("minecraft_scenic", ""), ("minecraft_pmmos", "pmmos/"), ("minecraft_dantdm", "dantdm/"))


def _growth_libraries() -> dict:
    """B-roll + music libraries with usage attribution for the Libraries tab.
    B-roll usage is filesystem-encoded (used_tapeN/ subdirs); music usage comes
    from the tape registry + music_history."""
    reg = _growth_registry()
    used_by_music: dict[str, list] = {}
    for t in reg.get("tapes", []):
        m = t.get("music")
        if m:
            p = str(t.get("part", "?"))
            tag = f"Loose {p[1:]}" if p.upper().startswith("L") else f"Part {p}"
            used_by_music.setdefault(m, []).append(tag)
    hist = reg.get("music_history", {})
    music = []
    for name in reg.get("music", []):
        used = ", ".join(used_by_music.get(name, [])) or hist.get(name, "")
        music.append({"name": name, "used_in": used})
    broll = []
    for game, pre in GROWTH_BROLL_GAMES:
        bdir = Path.home() / "agentic_os" / "broll_cache" / game
        if not bdir.is_dir():
            continue
        for f in sorted(bdir.glob("*.mp4")):
            broll.append({"name": pre + f.name, "used_in": "",
                          "size_mb": round(f.stat().st_size / 1e6)})
        for f in sorted((bdir / "reserve").glob("*.mp4")):
            broll.append({"name": f"{pre}reserve/{f.name}", "used_in": "",
                          "size_mb": round(f.stat().st_size / 1e6)})
        for sub in sorted(bdir.glob("used_*")):
            if sub.name.startswith("used_tape"):
                label = "Part " + sub.name.replace("used_tape", "")
            elif sub.name.startswith("used_loose"):
                label = "Loose " + (sub.name.replace("used_loose", "").lstrip("0") or "?")
            else:
                label = sub.name
            for f in sorted(sub.glob("*.mp4")):
                used = ("master (split into halves)" if f.stem == "full_original"
                        else label)
                broll.append({"name": pre + f.name, "used_in": used,
                              "size_mb": round(f.stat().st_size / 1e6)})
    return {"music": music, "broll": broll}


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
    p = subprocess.Popen(["/bin/sh", "-c", spec["cmd"]], cwd=AGENTIC_DIR, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         start_new_session=True)
    with _PROC_LOCK:
        RUNNING_PROCS[agent] = p; KILLED.discard(agent)
    try:
        out, errs = p.communicate(timeout=SCRIPT_TIMEOUT)
    finally:
        with _PROC_LOCK:
            RUNNING_PROCS.pop(agent, None)
    with _PROC_LOCK:
        was_killed = agent in KILLED; KILLED.discard(agent)
    if was_killed:
        return "⏹ Run stopped by you — partial work may already have completed (check Telegram/Gmail drafts)."
    class proc: returncode = p.returncode
    raw = (out or "").strip()
    err = (errs or "").strip()
    if err:
        raw += "\n[stderr]\n" + err
    raw = raw[-6000:].strip() or "(script produced no output)"
    status = "completed" if proc.returncode == 0 else f"exited with code {proc.returncode}"
    LAST_RUNS[agent] = {"log": raw, "report": "", "ts": time.time()}  # chat context

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
            LAST_RUNS[agent]["report"] = report
            return report
    except Exception:
        pass
    return f"Run {status}. Raw output:\n\n{raw}"


def trigger_job_apply() -> str:
    """Run fill_scouted.py directly (fire-and-forget) and return a descriptive
    feed message. The fill runs on THIS computer and needs the user's attention:
    every window is left open for review, résumé upload, and manual submit.
    (Was an n8n webhook; n8n retired 2026-06-13 — the bridge runs the script.)"""
    import threading

    jobs = []
    try:
        with open(SCOUT_JOBS) as f:
            data = json.load(f)
        jobs = data if isinstance(data, list) else data.get("jobs", [])
    except Exception:
        jobs = []

    def _fire():
        try:
            subprocess.run(["/bin/sh", "-c", "python3 fill_scouted.py"],
                           cwd=AGENTIC_DIR, timeout=1800)
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


# ── Sales pipeline: the editable cold-call sheet lives in the Obsidian vault.
# The Control Room reads + writes it through these helpers (owner-only, gated
# on the backend). Source of truth stays the markdown note Taran edits directly.
SALES_SHEET = (Path.home() / "Library" / "Mobile Documents" / "iCloud~md~obsidian" /
               "Documents" / "Digital Brain" / "Projects & Building" / "Piontrix Sales Pipeline.md")
SALES_BELOW = "<!-- SALES_AGENT_APPEND_BELOW"
SALES_ABOVE = "<!-- SALES_AGENT_APPEND_ABOVE -->"
SALES_STATUSES = ["🟣 To call", "🟡 Demo sent", "🔴 Call back", "🟢 WON", "⚫ Rejected", "⚪ Skip"]


def _sales_rows() -> list:
    """Parse the pipeline table (between the markers) into row dicts."""
    if not SALES_SHEET.exists():
        return []
    # businesses that already have a saved Gmail draft (✉ Draft button) — used to
    # hide the button so we don't re-draft the same place. Entry values carry the
    # email the draft attempt resolved ("" = searched, nothing found).
    # sales_emails.json holds backfill lookups that did NOT save a draft
    # (email_backfill.py) — merged so the UI can flag no-email rows either way.
    drafted, looked = {}, {}
    try:
        _df = Path.home() / ".pais" / "sales_drafted.json"
        if _df.exists():
            drafted = json.loads(_df.read_text())
    except Exception:
        drafted = {}
    try:
        _ef = Path.home() / ".pais" / "sales_emails.json"
        if _ef.exists():
            looked = json.loads(_ef.read_text())
    except Exception:
        looked = {}
    rows = []
    in_p = False
    for line in icloud_read.read_text(SALES_SHEET).splitlines():
        s = line.strip()
        if SALES_BELOW in s:
            in_p = True
            continue
        if SALES_ABOVE in s:
            break
        if not (in_p and s.startswith("|")):
            continue
        cols = [c.strip() for c in s.strip("|").split("|")]
        if len(cols) < 2 or not cols[1] or cols[1].lower() == "business" or re.fullmatch(r"-+", cols[1]):
            continue
        key = cols[1].strip().lower()
        email = (((drafted.get(key) or {}).get("email")
                  or (looked.get(key) or {}).get("email")) or "").strip()
        rows.append({
            "status": cols[0],
            "business": cols[1],
            "vertical": cols[2] if len(cols) > 2 else "",
            "phone": cols[3] if len(cols) > 3 else "",
            "window": cols[4] if len(cols) > 4 else "",
            "workflow": cols[5] if len(cols) > 5 else "",
            "notes": cols[7] if len(cols) > 7 else "",
            "added": cols[8] if len(cols) > 8 else "",
            "drafted": key in drafted,
            "email": email,
            # searched (draft attempt or backfill) and nothing was found
            "no_email": (key in drafted or key in looked) and not email,
        })
    return rows


def _sales_set_status(business: str, status: str) -> bool:
    """Change one row's Status cell in the vault sheet, atomically. Only the
    matching row is touched — every other line is left byte-identical."""
    if status not in SALES_STATUSES or not SALES_SHEET.exists():
        return False
    out, in_p, changed = [], False, False
    for line in icloud_read.read_text(SALES_SHEET).splitlines(keepends=True):
        s = line.strip()
        if SALES_BELOW in s:
            in_p = True
        elif SALES_ABOVE in s:
            in_p = False
        if in_p and not changed and s.startswith("|"):
            cols = [c.strip() for c in s.strip("|").split("|")]
            if len(cols) >= 2 and cols[1].lower() != "business" and cols[1].lower() == business.strip().lower():
                cols[0] = status
                line = "| " + " | ".join(cols) + " |" + ("\n" if line.endswith("\n") else "")
                changed = True
        out.append(line)
    if changed:
        tmp = str(SALES_SHEET) + ".tmp"
        Path(tmp).write_text("".join(out), encoding="utf-8")
        os.replace(tmp, str(SALES_SHEET))
    return changed


# ── Claude account switcher (owner-only, via the Control Room). Wraps the local
# `claude-account.sh`, which swaps the macOS-keychain Claude login. Switching here
# re-points this Mac's whole Claude subscription (interactive + every PAIS agent).
_SWITCHER = str(Path.home() / ".claude-accounts" / "claude-account.sh")


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
        if self.path not in ("/llm", "/search", "/stats", "/run-agent", "/leads", "/kill", "/sales-status", "/sales-draft", "/job-status", "/job-fill", "/linkedin-status", "/account", "/account-switch", "/trader-signal", "/trader-positions", "/trader-close", "/trader-scale", "/clip-status", "/clip-open", "/clip-draft", "/agent-chat"):
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

            # Jobs = the MERGED scout + apply agent. On-demand reuses the exact
            # pais-runtime runner (scout fresh roles → vault pipeline → Gemini fill
            # with open-tab fallback → mark Applied), so it matches the scheduled
            # morning routine. 'career'/'apply' are back-compat aliases.
            if agent in ("jobs", "career", "apply"):
                if pais_agents is not None:
                    try:
                        if agent == "apply":
                            text = pais_agents.run_apply({}, fields, persona)
                            return self._send(200, {"text": text})
                        # SCOUT is slow (~6-7 min: WebSearch + WebFetch + live-URL
                        # gate). Run it DETACHED so this request returns immediately
                        # instead of the UI timing out and looking failed. The worker
                        # appends new roles to the pipeline and posts the result to
                        # the feed when it finishes.
                        sp = pais_agents._spawn_scout(fields, persona)
                        if sp.get("ok"):
                            return self._send(200, {"text":
                                "🔍 Scouting fresh roles now — this runs in the "
                                "background (about a few minutes), so it won't time "
                                "out. New matches will appear in your Job Pipeline and "
                                "I'll post the results to your feed the moment it's "
                                "done. You can keep using the app meanwhile."})
                        # spawn failed → fall back to the old synchronous run
                        res = pais_agents.run_jobs({}, fields, persona)
                        text = res.get("text", "") if isinstance(res, dict) else str(res)
                        return self._send(200, {"text": text})
                    except subprocess.TimeoutExpired:
                        return self._send(504, {"error": "jobs agent timed out"})
                    except Exception as e:
                        return self._send(502, {"error": str(e)[:300]})
                if agent != "career":          # no merged runner → fill-only fallback
                    try:
                        return self._send(200, {"text": trigger_job_apply()})
                    except Exception as e:
                        return self._send(502, {"error": str(e)[:300]})
                # career falls through to its scout script below

            # Clips = detached worker (a daily run outlives SCRIPT_TIMEOUT). Ack
            # immediately; the worker posts the finished queue to the feed.
            if agent == "clips" and not data.get("simulate"):
                try:
                    return self._send(200, {"text": spawn_clips_worker()})
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
                text = run_claude(prompt, model) if not tools else _run_claude_tools(prompt, model, tools)
                self._send(200, {"text": text})
                if agent == "reviewer" and text.strip():
                    _telegram_reviewer(text)
            except subprocess.TimeoutExpired:
                self._send(504, {"error": "agent timed out"})
            except Exception as e:
                self._send(502, {"error": str(e)[:300]})
            return

        if self.path == "/kill":
            agent = (data.get("agent") or "").strip()
            with _PROC_LOCK:
                p = RUNNING_PROCS.get(agent)
                if p:
                    KILLED.add(agent)
            if p:
                try:
                    import signal
                    os.killpg(os.getpgid(p.pid), signal.SIGTERM)
                except Exception:
                    pass
                return self._send(200, {"ok": True})
            return self._send(200, {"ok": False, "message": "nothing running"})

        if self.path == "/leads":
            # Per-agent pipeline data (sanitized) for the web UI.
            agent = (data.get("agent") or "outreach").strip()
            try:
                items = []
                if agent == "outreach":
                    with open(os.path.join(AGENTIC_DIR, "piontrix_leads.json")) as f:
                        d = json.load(f)
                    for l in (d if isinstance(d, list) else d.get("leads", [])):
                        items.append({"title": l.get("business",""), "sub": l.get("website",""),
                                      "phone": l.get("phone",""), "done": bool(l.get("contacted")),
                                      "note": str(l.get("last_result",""))[:60]})
                elif agent in ("jobs", "career", "apply"):
                    if job_sheet is not None:        # vault Job Pipeline = source of truth
                        for r in job_sheet.rows():
                            st = r["status"]
                            note = (f"match {r['match']}" if r["match"] else "")
                            if r["applied"]:
                                note = (note + " · " if note else "") + f"applied {r['applied']}"
                            items.append({"title": r["company"], "sub": r["role"],
                                          "role": r["role"], "match": r["match"],
                                          "posted": r.get("posted", ""), "added": r.get("added", ""),
                                          "url": r["url"], "status": st,
                                          "done": st in (job_sheet.APPLIED_STATUS, "📞 Interview", "🎯 Offer"),
                                          "note": note, "notes": r["notes"]})
                    else:                            # legacy fallback: raw scout cache
                        with open(SCOUT_JOBS) as f:
                            d = json.load(f)
                        for j in (d if isinstance(d, list) else d.get("jobs", [])):
                            items.append({"title": j.get("company",""), "sub": j.get("role") or j.get("title",""),
                                          "role": j.get("role") or j.get("title",""),
                                          "match": str(j.get("match_score","")),
                                          "url": j.get("url",""), "done": False,
                                          "note": f"match {j.get('match_score','?')}"})
                elif agent == "linkedin":
                    if linkedin_sheet is not None:   # vault LinkedIn Pipeline = source of truth
                        for r in linkedin_sheet.rows():
                            st = r["status"]
                            note = str(r["why"])[:70]
                            if r["sent"]:
                                note = (note + " · " if note else "") + f"sent {r['sent']}"
                            items.append({"title": r["name"], "sub": f"{r['role']} · {r['company']}",
                                          "status": st, "added": r.get("added", ""),
                                          "done": st in ("🤝 Connected", "💬 Replied"),
                                          "note": note, "company": r["company"], "connect": r["connect"]})
                    else:                            # legacy fallback: raw queue json
                        with open(os.path.join(AGENTIC_DIR, "linkedin_pais_queue.json")) as f:
                            d = json.load(f)
                        for t in (d if isinstance(d, list) else d):
                            items.append({"title": t.get("name",""), "sub": f"{t.get('role','')} · {t.get('company','')}",
                                          "done": bool(t.get("contacted")), "note": str(t.get("why",""))[:60]})
                elif agent == "sales":
                    for r in _sales_rows():
                        st = r["status"]
                        items.append({"title": r["business"], "sub": r["vertical"],
                                      "phone": r["phone"], "status": st, "done": "WON" in st,
                                      "added": r.get("added", ""),
                                      "note": r["workflow"] or r["window"], "notes": r["notes"],
                                      "drafted": r.get("drafted", False),
                                      "email": r.get("email", ""),
                                      "no_email": r.get("no_email", False)})
                elif agent == "clips" and clip_sheet is not None:
                    # vault Clip Pipeline = source of truth; campaign link + ready-to-paste
                    # caption per creator (some campaigns ban hashtags — "hashtags" cfg)
                    def _thumb(creator_, clip_, src_path=None):
                        """Tiny poster frame as a data URI (cached under ~/.pais)."""
                        try:
                            tdir = Path.home() / ".pais" / "clip_thumbs"
                            tdir.mkdir(parents=True, exist_ok=True)
                            tp = tdir / f"{clip_}.jpg"
                            if not tp.exists():
                                src = (Path(src_path) if src_path else
                                       Path.home() / "Desktop" / "Clips" /
                                       creator_ / f"{clip_}.mp4")
                                if not src.exists():
                                    return ""
                                import imageio_ffmpeg
                                subprocess.run(
                                    [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-ss", "3",
                                     "-i", str(src), "-frames:v", "1",
                                     "-vf", "scale=-2:160", "-q:v", "7", str(tp)],
                                    capture_output=True, timeout=25)
                            if tp.exists():
                                import base64
                                return ("data:image/jpeg;base64," +
                                        base64.b64encode(tp.read_bytes()).decode())
                        except Exception:
                            pass
                        return ""
                    ccfg = {}
                    try:
                        with open(os.path.join(AGENTIC_DIR, "creators.json")) as f:
                            ccfg = json.load(f)
                    except Exception:
                        pass
                    for r in clip_sheet.rows():
                        st = r["status"]
                        if "Skip" in st:   # skipped clips stay in the vault note, drop off the queue
                            continue
                        cfg = ccfg.get(r["creator"], {})
                        tags = cfg.get("hashtags", f"#{r['creator']} #fyp #viral")
                        items.append({"title": r["hook"], "sub": f"{r['creator']} · {r['clip']}",
                                      "clip": r["clip"], "creator": r["creator"],
                                      "caption": (r["hook"] + " " + tags).strip(),
                                      "status": st, "added": r.get("date", ""),
                                      "done": st in ("📤 Submitted", "💰 Paid"),
                                      "url": cfg.get("campaign", ""),
                                      "thumb": _thumb(r["creator"], r["clip"]),
                                      "post": r.get("post", ""), "note": r.get("notes", "")})
                    # growth-niche tapes (content_pipeline renders) join the queue
                    for t in _growth_registry().get("tapes", []):
                        st = t.get("status", "🎬 Rendered")
                        if "Skip" in st:
                            continue
                        items.append({"title": t.get("title", t["id"]),
                                      "sub": f"growth · {t['id']}",
                                      "clip": t["id"], "creator": "growth",
                                      "caption": t.get("caption", t.get("title", "")),
                                      "status": st, "added": t.get("rendered", ""),
                                      "done": st in ("📤 Submitted", "💰 Paid"),
                                      "url": "",
                                      "thumb": _thumb("growth", t["id"],
                                                      src_path=os.path.expanduser(t["file"])),
                                      "post": t.get("posted", ""),
                                      "note": f"♪ {t.get('music', '?')} · 🎞 {t.get('broll', '?')}"})
                statuses = None
                if agent == "sales":
                    statuses = SALES_STATUSES
                elif agent in ("jobs", "career", "apply") and job_sheet is not None:
                    statuses = job_sheet.STATUSES
                elif agent == "clips" and clip_sheet is not None:
                    statuses = clip_sheet.STATUSES
                elif agent == "linkedin" and linkedin_sheet is not None:
                    statuses = linkedin_sheet.STATUSES
                payload = {"items": items, "statuses": statuses}
                if agent == "clips":
                    try:  # B-roll + music libraries for the Libraries tab
                        payload["libraries"] = _growth_libraries()
                    except Exception:
                        pass
                    try:  # hourly tiktok_stats.py summary → Views·24h chips
                        stats = json.loads((Path.home() / ".pais" / "tiktok_stats.json")
                                           .read_text()).get("summary")
                        if stats:
                            vmp3 = Path.home() / ".pais" / "clips_voice.mp3"
                            if vmp3.exists():  # Adam speaks the 24h numbers
                                import base64
                                stats["voice"] = base64.b64encode(vmp3.read_bytes()).decode()
                            payload["tiktok"] = stats
                    except Exception:
                        pass
                self._send(200, payload)
            except Exception as e:
                self._send(200, {"items": [], "error": str(e)[:100]})
            return

        if self.path == "/sales-status":
            biz = (data.get("business") or "").strip()
            status = (data.get("status") or "").strip()
            if not biz or status not in SALES_STATUSES:
                return self._send(400, {"error": "business + valid status required"})
            try:
                return self._send(200, {"ok": _sales_set_status(biz, status)})
            except Exception as e:
                return self._send(502, {"error": str(e)[:200]})

        if self.path == "/job-status":
            # Owner edits a job row's status in the Control Room → write it straight
            # to the vault Job Pipeline note (keyed by the posting URL).
            url = (data.get("url") or "").strip()
            status = (data.get("status") or "").strip()
            if not url or job_sheet is None or status not in job_sheet.STATUSES:
                return self._send(400, {"error": "url + valid status required"})
            try:
                return self._send(200, {"ok": job_sheet.set_status(url, status)})
            except Exception as e:
                return self._send(502, {"error": str(e)[:200]})

        if self.path == "/clip-status":
            # Owner flips a clip's status (Posted/Submitted/Paid) in the Control Room
            # → write it straight to the vault Clip Pipeline note (keyed by clip stem).
            clip = (data.get("clip") or "").strip()
            status = (data.get("status") or "").strip()
            post = (data.get("post") or "").strip() or None
            if not clip or clip_sheet is None or status not in clip_sheet.STATUSES:
                return self._send(400, {"error": "clip + valid status required"})
            if _growth_tape(clip):  # growth tapes track status in growth_tapes.json
                return self._send(200, {"ok": _growth_mark(clip, status, post)})
            try:
                return self._send(200, {"ok": clip_sheet.mark(clip, status, post)})
            except Exception as e:
                return self._send(502, {"error": str(e)[:200]})

        if self.path == "/clip-open":
            # ▶ in the Control Room → open the rendered clip on this Mac.
            # Path is derived from the SHEET row (never from raw input) so a
            # hostile clip value can't traverse anywhere.
            clip = (data.get("clip") or "").strip()
            if not clip:
                return self._send(400, {"error": "clip required"})
            if clip.startswith("lib:"):
                # ▶ on a Libraries row → open that music/B-roll file on this Mac.
                # Resolved ONLY by exact match against the scanned library —
                # a hostile name can't traverse anywhere.
                _, kind, name = (clip.split(":", 2) + ["", ""])[:3]
                libs = _growth_libraries()
                if not any(r["name"] == name for r in libs.get(kind, [])):
                    return self._send(404, {"error": "not in the library"})
                rel = name
                if kind == "music":
                    base = Path(AGENTIC_DIR) / "music_cache"
                else:
                    game = "minecraft_scenic"
                    if rel.startswith("pmmos/"):
                        game, rel = "minecraft_pmmos", rel[len("pmmos/"):]
                    elif rel.startswith("dantdm/"):
                        game, rel = "minecraft_dantdm", rel[len("dantdm/"):]
                    base = Path.home() / "agentic_os" / "broll_cache" / game
                path = base / rel
                if not path.exists() and kind == "broll":
                    hits = sorted(base.glob(f"*/{rel}"))  # used_tapeN/ subdirs
                    if hits:
                        path = hits[0]
                if not path.exists():
                    return self._send(404, {"error": "file missing on Mac"})
                subprocess.run(["open", str(path)], check=False)
                return self._send(200, {"ok": True})
            tape = _growth_tape(clip)   # path from the registry, never raw input
            row = None
            if tape is None and clip_sheet is not None:
                row = next((r for r in clip_sheet.rows() if r["clip"] == clip), None)
            if tape is None and row is None:
                return self._send(404, {"error": "clip not in sheet"})
            path = (Path(os.path.expanduser(tape["file"])) if tape else
                    Path.home() / "Desktop" / "Clips" / row["creator"] / f"{row['clip']}.mp4")
            if not path.exists():
                return self._send(404, {"error": "file missing on Mac"})
            subprocess.run(["open", str(path)], check=False)
            return self._send(200, {"ok": True})

        if self.path == "/clip-draft":
            # 📱 in the Control Room → upload this clip to TikTok as a DRAFT
            # (headed browser on this Mac; login persists in the tiktok profile).
            clip = (data.get("clip") or "").strip()
            if not clip:
                return self._send(400, {"error": "clip required"})
            in_sheet = clip_sheet is not None and any(
                r["clip"] == clip for r in clip_sheet.rows("Rendered"))
            tape = _growth_tape(clip)
            if not in_sheet and not (tape and "Rendered" in tape.get("status", "")):
                return self._send(404, {"error": "clip not in queue (must be 🎬 Rendered)"})
            with _PROC_LOCK:
                p = RUNNING_PROCS.get("_tiktok_draft")
                if p is not None and p.poll() is None:
                    return self._send(409, {"error": "another TikTok upload is still "
                                                     "running — wait for it to finish"})
                log = open("/tmp/pais_clip_draft.log", "a")
                proc = subprocess.Popen(
                    [sys.executable, "clip_pipeline.py", "autopost", clip, "draft"],
                    cwd=AGENTIC_DIR, stdout=log, stderr=log, start_new_session=True)
                RUNNING_PROCS["_tiktok_draft"] = proc
            return self._send(200, {"text": "📱 Uploading to TikTok as a draft — a "
                                            "browser window is opening on your Mac. "
                                            "You'll get a Telegram when it's saved."})

        if self.path == "/agent-chat":
            # Follow-up chat with an agent after a run: a claude session seeded with
            # the agent's last run log, resumable via session_id (claude -p --resume).
            agent = (data.get("agent") or "").strip()
            message = (data.get("message") or "").strip()
            sid = (data.get("session_id") or "").strip()
            if not agent or not message:
                return self._send(400, {"error": "agent + message required"})
            try:
                if sid:
                    cmd = ["claude", "-p", "--resume", sid, "--model", DEFAULT_MODEL,
                           "--output-format", "json", message]
                else:
                    lr = LAST_RUNS.get(agent) or {}
                    ctx = ""
                    if lr.get("log"):
                        ctx = "LAST RUN LOG:\n" + lr["log"][-4000:]
                        if lr.get("report"):
                            ctx += "\n\nREPORT POSTED TO FEED:\n" + lr["report"][-2000:]
                    elif agent == "clips" and os.path.exists("/tmp/pais_clips_last.txt"):
                        ctx = "LAST RUN LOG:\n" + Path("/tmp/pais_clips_last.txt").read_text()[-4000:]
                    seed = (
                        f"You are the user's '{agent}' agent on their personal AI team. "
                        f"They just watched you run and want to ask follow-up questions. "
                        f"Answer concretely from the run context below — name real items, "
                        f"numbers and links from it. If asked something outside the run, "
                        f"answer from your role. Keep replies tight.\n\n"
                        f"{ctx or '(no recent run log in memory — answer from your role)'}"
                        f"\n\nUser: {message}"
                    )
                    cmd = ["claude", "-p", "--model", DEFAULT_MODEL,
                           "--output-format", "json", seed]
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                      timeout=150, cwd=AGENTIC_DIR)
                if proc.returncode != 0:
                    raise RuntimeError((proc.stderr or "claude failed").strip()[:300])
                out = json.loads(proc.stdout)
                return self._send(200, {"text": (out.get("result") or "").strip(),
                                        "session_id": out.get("session_id") or sid})
            except subprocess.TimeoutExpired:
                return self._send(504, {"error": "chat timed out"})
            except Exception as e:
                return self._send(502, {"error": str(e)[:300]})

        if self.path == "/job-fill":
            # Owner presses 'Fill' on a pipeline row → run the detached fill worker
            # for JUST that job on this Mac. Reuses pais-runtime's on-demand path so
            # the fill is byte-identical to the old routine fill, one job at a time.
            url = (data.get("url") or "").strip()
            if not url:
                return self._send(400, {"error": "url required"})
            if pais_agents is None or not hasattr(pais_agents, "_fill_one_url"):
                return self._send(503, {"error": "fill runner unavailable on this host"})
            try:
                res = pais_agents._fill_one_url(url)
            except Exception as e:
                return self._send(502, {"error": str(e)[:200]})
            if res.get("ok"):
                who = f"{res.get('company','?')} — {res.get('role','')}".strip(" —")
                return self._send(200, {"ok": True,
                    "message": f"Filling {who} on your Mac — the form opens on your "
                               "screen for review. Nothing is submitted automatically."})
            return self._send(409, {"ok": False, "error": res.get("error", "fill failed")})

        if self.path == "/sales-draft":
            # Owner presses '✉ Draft' on a Sales pipeline row → spawn the detached
            # draft worker for that business (finds email, drafts the pitch, saves a
            # Gmail draft, posts the result to the feed). Never sends.
            business = (data.get("business") or "").strip()
            vertical = (data.get("vertical") or "").strip()
            if not business:
                return self._send(400, {"error": "business required"})
            if pais_agents is None or not hasattr(pais_agents, "_draft_one_business"):
                return self._send(503, {"error": "draft runner unavailable on this host"})
            try:
                res = pais_agents._draft_one_business(business, vertical)
            except Exception as e:
                return self._send(502, {"error": str(e)[:200]})
            if res.get("ok"):
                return self._send(200, {"ok": True,
                    "message": f"Drafting an email for {business} on your Mac — I'll save "
                               "it to Gmail → Drafts and post here when it's ready. Nothing "
                               "is sent automatically."})
            return self._send(409, {"ok": False, "error": res.get("error", "draft failed")})

        if self.path == "/linkedin-status":
            # Owner edits a LinkedIn target's status → write to the vault note
            # (keyed by name + company).
            name = (data.get("name") or "").strip()
            company = (data.get("company") or "").strip()
            status = (data.get("status") or "").strip()
            if not name or linkedin_sheet is None or status not in linkedin_sheet.STATUSES:
                return self._send(400, {"error": "name + valid status required"})
            try:
                return self._send(200, {"ok": linkedin_sheet.set_status(name, company, status)})
            except Exception as e:
                return self._send(502, {"error": str(e)[:200]})

        if self.path == "/trader-signal":
            # Owner pastes a Dr. Profit signal in the Control Room → local PAIS
            # server parses it (Claude subprocess) + sizes at fixed $60 risk and
            # creates the trade entry. Slow (~30-90s: one claude -p run).
            text = (data.get("text") or "").strip()
            if not text:
                return self._send(400, {"error": "signal text required"})
            try:
                return self._send(200, _local_pais(
                    "/api/live/signal", payload={"text": text}, timeout=180))
            except Exception as e:
                return self._send(502, {"error": str(e)[:300]})

        if self.path == "/trader-close":
            # Owner presses Close on a desk position → realize it in the tracker at
            # the live mark (or an override pnl/exit if the fill differed).
            tid = (data.get("id") or "").strip()
            if not tid:
                return self._send(400, {"error": "trade id required"})
            body = {"pnl": data.get("pnl", 0)}
            if data.get("exit_price") is not None:
                body["exit_price"] = data["exit_price"]
            try:
                return self._send(200, _local_pais(
                    f"/api/trades/{tid}/close", payload=body, timeout=20))
            except Exception as e:
                return self._send(502, {"error": str(e)[:300]})

        if self.path == "/trader-scale":
            # Owner presses + / − on a desk position → scale it in ('add') or
            # out ('reduce') in the tracker at a given fill price.
            tid = (data.get("id") or "").strip()
            if not tid:
                return self._send(400, {"error": "trade id required"})
            body = {
                "action": data.get("action"),
                "units":  data.get("units"),
                "price":  data.get("price"),
            }
            try:
                return self._send(200, _local_pais(
                    f"/api/trades/{tid}/scale", payload=body, timeout=20))
            except Exception as e:
                return self._send(502, {"error": str(e)[:300]})

        if self.path == "/trader-positions":
            # Open positions marked to live prices + bankroll analytics for the
            # Control Room trader view. Read-mostly; prices are TTL-cached locally.
            try:
                live = _local_pais("/api/live/prices", method="GET", timeout=30)
                trades = _local_pais("/api/trades", method="GET", timeout=10)
                marks = {p["id"]: p for p in live.get("positions", [])}
                items = []
                for t in trades.get("active", []):
                    m = marks.get(t["id"], {})
                    items.append({
                        "id":          t["id"],
                        "asset":       t.get("asset"),
                        "asset_class": t.get("asset_class", "crypto"),
                        "direction":   t.get("direction"),
                        "entry":       t.get("entry_price"),
                        "stop_loss":   t.get("stop_loss"),
                        "take_profit": t.get("take_profit") or [],
                        "leverage":    t.get("leverage", 1),
                        "units":       t.get("position_size"),
                        "notional":    t.get("notional"),
                        "risk_usd":    t.get("risk_usd"),
                        "mark":        m.get("mark"),
                        "pnl":         m.get("pnl", t.get("pnl")),
                        "pnl_pct":     m.get("pnl_pct"),
                        "r_multiple":  m.get("r_multiple"),
                        "opened_at":   (t.get("opened_at") or "")[:10],
                    })
                closed = [t for t in trades.get("closed", [])
                          if t.get("status") != "cancelled"]
                return self._send(200, {
                    "positions": items,
                    "bankroll":  live.get("bankroll") or trades.get("bankroll_summary"),
                    "recent_closed": [{
                        "asset": t.get("asset"), "direction": t.get("direction"),
                        "pnl": t.get("pnl"), "risk_usd": t.get("risk_usd"),
                        "closed_at": (t.get("closed_at") or "")[:10],
                    } for t in closed[-50:]],
                })
            except Exception as e:
                return self._send(502, {"error": str(e)[:300]})

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

        if self.path == "/account":
            # Active Claude login + saved profiles + live quota, in one call.
            try:
                res = subprocess.run([_SWITCHER, "json"], capture_output=True,
                                     text=True, timeout=8)
                info = (json.loads(res.stdout)
                        if res.returncode == 0 and res.stdout.strip()
                        else {"accounts": [], "error": (res.stderr or "switcher unavailable").strip()})
            except Exception as e:
                info = {"accounts": [], "error": str(e)[:200]}
            try:
                from tools.usage_quota import fetch_quota
                info["quota"] = fetch_quota()                       # active account (back-compat)
                # Attach each account's own quota so the Control Room can show a
                # non-active account's reset times WITHOUT switching to it.
                for a in info.get("accounts", []):
                    try:
                        a["quota"] = fetch_quota(account=None if a.get("active") else a.get("name"))
                    except Exception:
                        a["quota"] = None
            except Exception:
                info["quota"] = None
            return self._send(200, info)

        if self.path == "/account-switch":
            name = (data.get("name") or "").strip()
            if not name:
                return self._send(400, {"error": "name required"})
            try:
                res = subprocess.run([_SWITCHER, "use", name], capture_output=True,
                                     text=True, timeout=15)
            except Exception as e:
                return self._send(502, {"error": str(e)[:200]})
            if res.returncode != 0:
                return self._send(409, {"ok": False,
                                        "error": (res.stderr or res.stdout).strip()})
            try:
                from tools.usage_quota import fetch_quota
                fetch_quota(force=True)   # refresh cache so the next /account is fresh
            except Exception:
                pass
            return self._send(200, {"ok": True, "message": res.stdout.strip()})

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
