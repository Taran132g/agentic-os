# PAIS Local-Business Workflows — Architecture

How the agents run on a customer's machine, talk to the PAIS website, and what
each one actually does. Reference build for the Trappe Tavern demo dashboard.

---

## 1. The deployment model (why it's cheap + compliant)

```
  CUSTOMER'S MAC / MINI-PC (Trappe Tavern back office)
  ┌──────────────────────────────────────────────────────────┐
  │  pais-runtime  (one Python process, launchd-managed)       │
  │   • runs agents via `claude -p`  → THEIR Claude sub        │
  │   • no Anthropic API key, no per-token bill to you         │
  │   • cron schedule (overnight) + on-demand webhook          │
  │                                                            │
  │   agents/                                                  │
  │     reputation_workflow.py                                 │
  │     reactivation_workflow.py                               │
  │     reminders_workflow.py                                  │
  │     missedcall_workflow.py                                 │
  │     digest_workflow.py                                     │
  │                                                            │
  │   tools/  (shared)                                         │
  │     reviews.py     google_business.py   sms.py            │
  │     contacts.py    pos.py (Toast/Square) approval.py      │
  │     state.py (local SQLite)                                │
  └─────────────┬───────────────────────────┬────────────────┘
                │ outbound HTTPS only        │ outbound HTTPS only
                ▼                            ▼
        PAIS WEBSITE (pais-site, Vercel)   OWNER'S PHONE (Telegram)
        ┌────────────────────────────┐    ┌────────────────────────┐
        │ /dashboard  (this demo)    │    │ approve / edit / skip   │
        │ • pulls runtime state      │    │ drafts in one tap       │
        │ • renders queues + ROI     │    │ owner is the SENDER →   │
        │ • owner approves drafts    │    │ no A2P 10DLC burden     │
        └────────────────────────────┘    └────────────────────────┘
```

**Key idea:** the runtime lives on *their* machine and uses *their* Claude
subscription. The PAIS website is a thin control surface — it reads runtime
state and posts approvals back. Nothing sensitive (customer lists, review text)
has to leave their box except the drafts the owner chooses to send.

### How the runtime reaches the PAIS website

The runtime never exposes an inbound port. It speaks to PAIS over outbound HTTPS:

1. **Heartbeat** — every 5 min the runtime `POST /api/runtime/heartbeat`
   `{ business_id, agents:[...], last_run }`. The website's "Agents online"
   pill is driven by this (26h staleness window, same as PAIS today).
2. **State push** — after each agent run the runtime pushes a state blob
   (`POST /api/runtime/state`): pending approvals, metrics, activity feed.
   The dashboard renders that blob. (Demo uses a static `state.json`.)
3. **Approval pull** — the runtime long-polls `GET /api/runtime/approvals`
   for owner decisions made in the dashboard, then executes them (send the
   email, post the review reply) on the owner's behalf.

So: **runtime = brain + hands** on their machine; **website = eyes + approve
button** the owner opens from anywhere. Same split PAIS already uses with the
Mac bridge — just pointed at a customer instead of Taran.

---

## 2. One agent = one workflow module

Every agent is a Python module matching the existing `*_workflow.py` shape:
a context prompt + ordered steps, run through `claude -p`. Shared structure:

```python
# agents/reputation_workflow.py
CONTEXT = """You are the Reputation agent for {business}. ..."""

def run(state, mode="scheduled"):
    new_reviews   = tools.reviews.fetch_since(state.last_review_cursor)   # scrape/API
    drafts        = llm.draft_replies(new_reviews, voice=state.brand_voice)
    asks          = tools.contacts.recent_visitors_without_review()
    review_asks   = llm.draft_review_requests(asks)
    state.queue_approvals(drafts + review_asks)        # → owner inbox
    state.bump_metrics(reviews_seen=len(new_reviews))
    return state
```

The orchestrator schedules them; results land in local SQLite (`state.py`),
get pushed to the website, and wait for owner approval before anything sends.

---

## 3. What each agent actually does

| Agent | Trigger | Reads | Produces (waits for approval) | Plugs the leak |
|-------|---------|-------|-------------------------------|----------------|
| **Reputation** | nightly + on new review | Google reviews, recent visitors | Owner-voiced review replies; review-request msgs to happy recent guests | Low star count / unanswered reviews killing "near me" rank |
| **Reactivation** | weekly | POS / booking history | Win-back offers to regulars who've gone quiet + first-timers who never returned | Regulars silently churning |
| **Reminders** | nightly (T-1 day) | reservation / appointment book | Confirmations + personal nudges for tomorrow's bookings; recall list for long-overdue guests | No-shows + empty tables |
| **Missed-Call** | real-time (missed call webhook) | call log | Instant text-back draft: "Sorry we missed you — can we book you?" | After-hours / busy-time lost leads |
| **Digest** | nightly | POS rollups | Plain-English morning brief: busiest hours, avg ticket, top items, what to prep | Flying blind / no owner visibility |

**Human-in-the-loop everywhere:** agents never send autonomously. They draft;
the owner taps approve (dashboard or Telegram); the runtime sends. For SMS the
owner's own number is the sender, which sidesteps A2P 10DLC / TCPA registration
that would otherwise block a solo builder. Email follows CAN-SPAM (real sender +
unsubscribe) and can largely auto-send once the owner trusts a flow.

---

## 4. Per-business config (what you set up at onboarding)

```jsonc
// business.json — one file per client
{
  "business_id": "trappe-tavern",
  "name": "Trappe Tavern",
  "vertical": "hospitality",
  "brand_voice": "warm, local, no corporate-speak; signs off 'see you at the bar'",
  "channels": { "email": true, "sms": "owner-send", "telegram_chat_id": "..." },
  "integrations": { "reviews": "google", "pos": null, "booking": "phone" },
  "agents": ["reputation", "reactivation", "missedcall", "digest"]
}
```

Trappe Tavern has **no first-party ordering and no POS integration**, so its
build leans on Reputation + Missed-Call + Reactivation (contacts gathered from
the reservation phone log), with Digest light until a POS is connected.

---

## 5. Demo vs. real

This folder is the **dashboard the owner sees** — wired to a static `state.json`
so every queue, number, and chart is interactive without a live runtime. In
production that same JSON is the blob the runtime pushes. Approve a draft here
and it animates out + updates the ROI counter, exactly as it will when the
runtime is on the other end.
