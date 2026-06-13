#!/bin/bash
# morning_stack.sh — single chained morning run for all PAIS scheduled jobs.
#
# Created 2026-06-09: consolidates the former staggered n8n crons (7:30am-8pm)
# into ONE wake window so the Mac is only up ~1 hour, then sleeps. Day-of-week
# guards keep each job on its original days. Manual webhook triggers
# (/webhook/apply, /outreach, /triage, /brainscan-outreach) are unaffected.
#
# Wake/sleep loop: power_cycle.py arms a single 07:25 pmset wake -> Mac wakes
# -> n8n 'morning-stack' cron (07:30) runs this script -> last line arms the
# next 07:25 wake and sleeps. Self-perpetuating.
#
# Each job is guarded so one failure never aborts the chain — the final sleep
# MUST always run, or the Mac would stay awake indefinitely.

set -u
cd /Users/taranveersingh/agentic_os || exit 1

mkdir -p logs

# Hold the machine fully awake for the ENTIRE chain. The 07:25 armed wake fires,
# but a closed lid (clamshell) — or battery power — degrades it to DarkWake and
# drops back to Sleep within ~45s. On 06-12/06-13 that truncated the chain after
# the digest (~18s of runtime; piontrix/repo-sync never ran). Re-exec once under
# caffeinate so a no-sleep assertion is held from the first command through the
# final deliberate sleep.
#   -i prevent idle system sleep   -m prevent disk idle sleep
#   -s prevent system sleep (AC only)   -u declare user active
# NOTE: caffeinate cannot beat clamshell sleep on BATTERY. Durable fix: keep the
# lid open during the 07:30 window, OR install the pmset sudoers rule + run on AC
# (then the disablesleep toggle below takes effect).
if [ -z "${MS_CAFFEINATED:-}" ]; then
  export MS_CAFFEINATED=1
  exec caffeinate -imsu "$0" "$@"
fi

# Robust persistent log. n8n keeps only the last stdout line, and the previous
# `exec > >(tee ...)` process-substitution got killed when the DarkWake window
# closed mid-run, so NO log survived (06-13). A plain append redirect is created
# up front and flushed line-by-line, leaving a diagnosable trail even if the run
# is later truncated.
exec >> logs/morning_stack.log 2>&1

# Pin every `claude -p` call in the stack to Sonnet (2026-05-18 PAIS cost
# decision). Without this they inherit ~/.claude/settings.json — i.e. whatever
# Taran last picked interactively with /model — which silently changes batch
# cost/usage. Interactive sessions are unaffected.
export ANTHROPIC_MODEL="claude-sonnet-4-6"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] morning-stack: $*"; }
dow=$(date +%u)   # 1=Mon … 7=Sun

log "START (dow=$dow)"

# Best-effort clamshell defeat: if the passwordless-pmset sudoers rule is present
# AND we're on AC, this lets the chain run with the lid CLOSED without dropping to
# sleep. `sudo -n` never prompts, so it silently no-ops when the rule is absent.
# Reset to 0 right before the deliberate sleep at the end.
if sudo -n pmset -c disablesleep 1 2>/dev/null; then
  log "clamshell sleep disabled for this run (AC)"
else
  log "disablesleep unavailable (need pmset sudoers rule + AC) — relying on caffeinate"
fi

python3 power_cycle.py wokeup || true

# --- daily: refresh the brain first so every job (and the day's digest/chat)
# --- searches an up-to-date index of the vault. Incremental — only changed notes.
log "brain-reindex (changed vault notes)"
python3 -c "from tools.rag import index_changed_files; index_changed_files()" || log "brain-reindex FAILED"

# --- daily ---
log "email-triage (act: label + archive promos)"
python3 email_triage.py act || log "email-triage FAILED"

log "vault-daily-digest"
python3 vault_digest.py || log "vault-daily-digest FAILED"

# --- weekdays only (apply-jobs scheduled run = scout only; fill stays manual) ---
if [ "$dow" -le 5 ]; then
  log "apply-jobs (scout)"
  python3 job_scout.py || log "apply-jobs FAILED"
else
  log "apply-jobs skipped (weekend)"
fi

# --- daily ---
log "piontrix-outreach"
python3 piontrix_scout.py || log "piontrix scout FAILED"
OUTREACH_GMAIL_DRAFT=1 python3 piontrix_outreach.py --batch || log "piontrix draft FAILED"
python3 backfill_phones.py || log "phone-find FAILED"

# --- weekdays only: one LinkedIn connect draft per day (manual send) ---
if [ "$dow" -le 5 ]; then
  log "linkedin-internship (1 draft)"
  python3 linkedin_pais.py || log "linkedin FAILED"
else
  log "linkedin-internship skipped (weekend)"
fi

# --- Mon/Wed/Sat only ---
case "$dow" in
  1|3|6) log "repo-sync"; python3 tools/repo_sync.py || log "repo-sync FAILED" ;;
  *)     log "repo-sync skipped (not Mon/Wed/Sat)" ;;
esac

log "DONE — arming next wake + sleeping"
sudo -n pmset -c disablesleep 0 2>/dev/null || true   # re-enable sleep before we sleep
python3 power_cycle.py sleep || log "power_cycle sleep FAILED"
