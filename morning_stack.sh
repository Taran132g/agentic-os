#!/bin/sh
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

# Pin every `claude -p` call in the stack to Sonnet (2026-05-18 PAIS cost
# decision). Without this they inherit ~/.claude/settings.json — i.e. whatever
# Taran last picked interactively with /model — which silently changes batch
# cost/usage. Interactive sessions are unaffected.
export ANTHROPIC_MODEL="claude-sonnet-4-6"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] morning-stack: $*"; }
dow=$(date +%u)   # 1=Mon … 7=Sun

log "START (dow=$dow)"
python3 power_cycle.py wokeup || true

# --- daily: refresh the brain first so every job (and the day's digest/chat)
# --- searches an up-to-date index of the vault. Incremental — only changed notes.
log "brain-reindex (changed vault notes)"
python3 -c "from tools.rag import index_changed_files; index_changed_files()" || log "brain-reindex FAILED"

# --- daily ---
log "email-triage"
python3 email_triage.py || log "email-triage FAILED"

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

log "brainscan-outreach"
OUTREACH_GMAIL_DRAFT=1 python3 brainscan_outreach.py || log "brainscan FAILED"

# --- weekdays only: one LinkedIn connect draft per day (manual send) ---
if [ "$dow" -le 5 ]; then
  log "linkedin-internship (1 draft)"
  OUTREACH_LIMIT=1 python3 linkedin_internship.py || log "linkedin draft FAILED"
else
  log "linkedin-internship skipped (weekend)"
fi

# --- Sunday only: weekly queue top-up reminder ---
if [ "$dow" -eq 7 ]; then
  log "linkedin queue reminder"
  python3 linkedin_internship.py reminder || log "linkedin reminder FAILED"
fi

# --- Mon/Wed/Sat only ---
case "$dow" in
  1|3|6) log "repo-sync"; python3 tools/repo_sync.py || log "repo-sync FAILED" ;;
  *)     log "repo-sync skipped (not Mon/Wed/Sat)" ;;
esac

log "DONE — arming next wake + sleeping"
python3 power_cycle.py sleep || log "power_cycle sleep FAILED"
