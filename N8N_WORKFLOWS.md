# PAIS n8n Automation Layer

n8n orchestrates the recurring PAIS jobs (job applications, outreach, digests,
email triage, repo sync). It runs **locally** on the Mac and calls the Python
scripts in this repo via Execute Command nodes.

- **Editor / UI:** http://localhost:5678 (also `http://100.115.22.97:5678` from
  any device on the Tailscale tailnet, e.g. Taran's iPhone)
- **Runtime:** launchd service `com.taranveer.n8n` (auto-starts on login, restarts
  on crash). Plist: `~/Library/LaunchAgents/com.taranveer.n8n.plist`
- **DB:** SQLite at `~/.n8n/database.sqlite`
- **Logs:** `~/agentic_os/logs/n8n.out.log` / `n8n.err.log` (gitignored)

## ⚠️ n8n 2.0 gotcha — Execute Command disabled by default
n8n 2.0 disables the `executeCommand` and `localFileTrigger` nodes for security.
All PAIS workflows depend on `executeCommand`, so the launchd plist sets:

```
NODES_EXCLUDE=["n8n-nodes-base.localFileTrigger"]
```

This overrides the default exclude list — re-enabling Execute Command while
keeping LocalFileTrigger off. Without it, every workflow fails to activate with
`Unrecognized node type: n8n-nodes-base.executeCommand`.

## Workflows

| Workflow | Trigger | Script | Status |
|----------|---------|--------|--------|
| **apply-jobs** | weekdays 9:00am (`0 9 * * 1-5`) + `POST /webhook/apply` | search → `jobsearch_cli.py` → fill each via `jobfill_cli.py` | active |
| **piontrix-outreach** | daily 9:45am (`45 9 * * *`) + `POST /webhook/outreach` | `piontrix_outreach.py` (batch / single) | active |
| **email-triage** | 7:30am & 6:30pm + `POST /webhook/triage` | `email_triage.py` (read-only digest) | active |
| **vault-daily-digest** | daily 8:00am (`0 8 * * *`) | `vault_digest.py` | active |
| **repo-sync** | Mon/Wed/Sat (`0 20 * * 1,3,6`) | `tools/repo_sync.py` | active |
| **content-rotation** | daily 11:00am (`0 11 * * *`) | `content_cron.py` | inactive |
| **ff-daily-digest** | daily 8:30am (`30 8 * * *`) | FindingFounders `scripts/daily_digest.py` | inactive |
| **jobfill** | `POST /webhook/jobfill` | `jobfill_cli.py` (single URL) | inactive |

## Script reference

- **`jobsearch_cli.py`** — searches The Muse + Remotive. `JOBSEARCH_SEARCH_ONLY=1`
  emits clean JSON (sanitized) for the apply-jobs pipeline to split + fill.
- **`jobfill_cli.py`** — fills ONE application URL via `tools/browser_fill.py`.
  `N8N_PING` arg = healthcheck (no Chrome).
- **`piontrix_outreach.py`** — research site → draft via `claude` → find email via
  Hunter.io → review (Telegram) or `send` (Gmail). `--batch` reads
  `piontrix_leads.json` (gitignored).
- **`email_triage.py`** — IMAP (app password) → classify via `claude` → Telegram
  digest. `act` arg also labels + archives clear promos. Default read-only.
- **`vault_digest.py`** — summarizes yesterday's vault session + open follow-ups.
- **`content_cron.py`** — rotates AITA/Stoic/Horror render by day.
- **`tools/repo_sync.py`** — commits + pushes Taran's own repos with a secret
  guard. See below.

## Editing workflows
Edit in the **web UI** (changes apply live, stay active). Editing via CLI
(`n8n import:workflow`) resets a workflow to inactive and needs:
```
n8n update:workflow --id=<id> --active=true
n8n publish:workflow --id=<id>
launchctl kickstart -k gui/$(id -u)/com.taranveer.n8n   # re-register triggers
```

## repo-sync safety
`tools/repo_sync.py` only touches an explicit allowlist of Taran-owned repos,
verifies each remote belongs to Taran, and runs a secret guard (filename
denylist + content scan) before pushing. It never touches others'/course repos
or the multi-clone `taranveer-singh.github.io`. See the script header for detail.
