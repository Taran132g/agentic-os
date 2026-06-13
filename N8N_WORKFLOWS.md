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

> **2026-06-09 — consolidated to a single morning window.** All scheduled jobs
> now run back-to-back from ONE 07:30 trigger (`morning-stack`) so the Mac is
> only awake ~1 hour, then sleeps. The old per-job crons (8am–8pm) were retired.
> Each job keeps its original *days* via day-guards inside `morning_stack.sh`.
> The webhook (manual) triggers are unchanged.

### The morning stack

`morning-stack` (n8n, cron `30 7 * * *`) runs `bash morning_stack.sh`, which
executes sequentially, each guarded so one failure never aborts the chain:

1. `power_cycle.py wokeup` (Telegram ☀️ ping)
2. `email_triage.py act` — daily (labels everything `Triaged/<Category>`, replacing any prior Triaged/* label, + archives clear promos; switched from read-only 2026-06-12)
3. `vault_digest.py` — daily
4. `job_scout.py` — **weekdays only** (scheduled apply = scout only; filling
   stays manual via `POST /webhook/apply` → `fill_scouted.py`)
5. `piontrix_scout.py` → `piontrix_outreach.py --batch` — daily
6. `brainscan_outreach.py` — daily
7. `linkedin_internship.py` (OUTREACH_LIMIT=1) — **weekdays only** (added 2026-06-10)
8. `linkedin_internship.py reminder` — **Sunday only** (queue top-up nag)
9. `tools/repo_sync.py` — **Mon/Wed/Sat only**
10. `power_cycle.py sleep` — arms next wake (or relies on `pmset repeat`), then sleeps

| Workflow | Trigger | Script | Status |
|----------|---------|--------|--------|
| **morning-stack** | daily 7:30am (`30 7 * * *`) | `morning_stack.sh` (chains all jobs ↑ then sleeps) | **active — the ONLY scheduled workflow** |
| **apply-jobs** | `POST /webhook/apply` (schedule node **removed** 2026-06-13) | `fill_scouted.py` (manual fill) | active (webhook only) |
| **piontrix-outreach** | `POST /webhook/outreach` (schedule node **removed** 2026-06-13) | `piontrix_outreach.py` (single) | active (webhook only) |
| **email-triage** | `POST /webhook/triage` (schedule node **removed** 2026-06-13) | `email_triage.py` | active (webhook only) |
| **brainscan-outreach** | `POST /webhook/brainscan-outreach` (workflow inactive) | `brainscan_outreach.py` | inactive (webhook def only) |
| ~~vault-daily-digest~~ | — | *(repurposed into `morning-stack`)* | renamed |
| ~~repo-sync~~ | — | now runs inside the stack | **deleted** 2026-06-09 |
| ~~power-sleep~~ | — | replaced by stack + `power_cycle.py` | **deleted** 2026-06-09 |
| **content-rotation** | daily 11:00am (`0 11 * * *`) | `content_cron.py` | inactive |
| **ff-daily-digest** | daily 8:30am (`30 8 * * *`) | FindingFounders `scripts/daily_digest.py` | inactive |
| **jobfill** | `POST /webhook/jobfill` | `jobfill_cli.py` (single URL) | inactive |

## 2026-06-13 — schedules truly consolidated (n8n disabled-trigger gotcha)

The 2026-06-09 "consolidation" *disabled* the standalone schedule nodes in the
editor, but **n8n does not honor `disabled: true` on a trigger node of an
already-active workflow** — the cron stays registered and keeps firing, and a
full n8n restart does NOT clear it. So `email-triage` (7:30am + 6:30pm),
`piontrix-outreach` (9:45am), and `apply-jobs` (`job_scout.py`, weekdays 9am)
had been firing daily the whole time, duplicating the in-stack jobs. They caused
no damage only because (a) the jobs are idempotent (`piontrix --batch` skips
`contacted` leads; triage relabel/archive is a no-op on repeat) and (b)
morning-stack was darkwake-truncating before it reached them.

**Fix:** the only reliable way to stop a schedule on an active workflow is to
**delete the scheduleTrigger node** (or deactivate the whole workflow — but that
kills the webhook too). Removed the schedule nodes from those three workflows via
direct DB edit (n8n stopped via `launchctl bootout`, edit `workflow_entity.nodes`
+ `connections`, decrement `triggerCount`, `launchctl bootstrap`). Webhook
triggers (`/webhook/triage`, `/outreach`, `/apply`) left intact. DB backed up to
`~/.n8n/database.backup-preschedremove-*.sqlite`. **morning-stack is now the only
scheduled workflow.**

## 2026-06-09 hardening (audit fixes)

- **Webhook shell-injection closed.** `jobfill` and `piontrix-outreach (single)`
  used to interpolate webhook body fields raw into Execute Command. Both now
  whitelist-strip shell metacharacters (`" $ \` \\ ; ( ) …`) in the n8n
  expression before the value reaches the shell, and missing fields become `''`
  instead of the literal string `undefined`. If you edit these nodes, keep the
  `.replace(/[^…]/g, '')` wrapper.
- **`email_triage.py`** — IMAP ops are now **UID-based** (sequence numbers
  aren't stable across the two connections act-mode uses, so labels/archives
  could hit the wrong mail); archive sends the correct `\Inbox` label (was
  double-escaped and silently failing); both connections have `timeout=60`
  (a Gmail stall used to hang the run forever); label/archive failures are
  counted and surfaced in the digest instead of `except: pass`.
- **`fill_scouted.py`** — exits 0 on partial success (exit 1 made n8n mark the
  whole execution errored; per-job status was already in the Telegram summary).
- **`piontrix_outreach.py` / `brainscan_outreach.py`** — batch runs only mark a
  lead `contacted` when the draft demonstrably reached Taran (Telegram delivery
  confirmed, falling back to plain text on HTML parse errors, or Gmail draft
  saved) or was actually sent. Undelivered/failed leads stay pending, capped at
  3 retries (`retry_count`).
- **`tools/repo_sync.py`** — DENY_NAMES now also blocks `brainscan_creators.json`,
  `linkedin_targets.json`, `applications.json`, `job_queue.json`, `scout_jobs.json`
  (real names/emails — defense-in-depth beyond .gitignore; repo is public).

### Wake/sleep — one-time setup required

The wake half of the loop needs root (`pmset`). The cleanest is a **recurring**
wake set once (survives reboots, no per-run sudo):

```bash
sudo pmset repeat wake MTWRFSU 07:25:00
```

`power_cycle.py WAKE_TIMES` is now a single daily `07:25`. `power_cycle.py sleep`
has a **safety guard**: it refuses to sleep unless a wake is scheduled (one-time
arm *or* a `pmset repeat wake`), so a missing sudo rule strands the Mac awake
(safe) instead of asleep (stuck). Until the `pmset repeat` above is set — or the
sudoers rule from this file's header is installed — the Mac will finish the stack
and stay awake with a Telegram ⚠️.

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

## BrainScan outreach (added 2026-06-05)

Companion to `piontrix-outreach`, for cold outreach to PKM/Obsidian creators &
newsletters. Same plumbing (reuses piontrix's Telegram + Gmail helpers), same
safety model: **review by default** (Telegram the draft; sending needs the
explicit `send` arg). Reads `brainscan_creators.json` (gitignored — real,
pre-verified PUBLIC emails only; emails are never guessed). Only handles
PENDING entries (`"contacted": false`) and marks them done, so repeated runs
never re-email anyone — add new creators to the JSON to queue more.

- **Script:** `brainscan_outreach.py` — `python3 brainscan_outreach.py` (review) /
  `... send` (send pending). Env: `OUTREACH_DRY=1`, `OUTREACH_GMAIL_DRAFT=1`,
  `OUTREACH_LIMIT=10`.
- **Data:** `brainscan_creators.json` = `[{"name","email","hook","contacted"}]`.

### Wire it to run RIGHT AFTER piontrix-outreach (n8n UI — edits apply live)
Two options:

1. **Chained (truest "right after"):** open the **piontrix-outreach** workflow,
   add a final **Execute Command** node after the last node:
   `python3 brainscan_outreach.py`
   and connect it so it fires when piontrix finishes. Save (stays active).

2. **Standalone, 5 min later:** duplicate piontrix-outreach, change the Execute
   Command to `python3 brainscan_outreach.py`, set the schedule to `50 9 * * *`
   (piontrix is `45 9`), add webhook `POST /webhook/brainscan-outreach`, save.

If you import/edit via CLI instead of the UI, re-activate per the doc:
```
n8n update:workflow --id=<id> --active=true && n8n publish:workflow --id=<id>
launchctl kickstart -k gui/$(id -u)/com.taranveer.n8n
```

| Workflow | Trigger | Script | Status |
|----------|---------|--------|--------|
| **brainscan-outreach** | inside `morning-stack` (daily) + `POST /webhook/brainscan-outreach` | `brainscan_outreach.py` | active (webhook; batch via stack) |

## LinkedIn internship outreach (added 2026-06-09)

Daily **drafting** helper for Taran's Summer-2027 internship networking — mirrors
piontrix/brainscan (Telegram review, reuses _tg_text). **Does NOT search/scrape
LinkedIn** (ToS + account-restriction risk; no connect API). You add people you
found via LinkedIn's own filters (Company → People → School: Penn State) to
`linkedin_targets.json`; the script drafts **one per day** — a <200-char
connection note + a post-accept message — to Telegram. You click Connect by hand.

- **Script:** `linkedin_internship.py` — `python3 linkedin_internship.py`.
  Env: `OUTREACH_LIMIT=1` (one/day), `OUTREACH_DRY=1` (preview, doesn't consume queue).
- **Data:** `linkedin_targets.json` (gitignored) =
  `[{"name","company","role","alum","profile_url","context"}]`.

### Wire it (n8n UI)
Duplicate a Schedule-triggered workflow (e.g. piontrix-outreach), set:
- **Schedule:** daily, e.g. `0 10 * * 1-5` (weekday mornings — 1 connect/day is a safe pace).
- **Execute Command:** `python3 linkedin_internship.py`
- Save (stays active). Each morning Telegram gets that day's connect note + follow-up to send manually.

| Workflow | Trigger | Script | Status |
|----------|---------|--------|--------|
| **linkedin-internship** | inside `morning-stack`, weekdays | `linkedin_internship.py` (LIMIT=1) | wired 2026-06-10 |

### Weekly queue top-up reminder
`python3 linkedin_internship.py reminder` Telegrams how many targets are ready +
how many blank company rows still need a name/URL. Wire a separate weekly
Schedule node: `0 18 * * 0` (Sun 6pm) → Execute Command `python3 linkedin_internship.py reminder`.

| Workflow | Trigger | Script | Status |
|----------|---------|--------|--------|
| **linkedin-queue-reminder** | inside `morning-stack`, Sundays | `linkedin_internship.py reminder` | wired 2026-06-10 |
