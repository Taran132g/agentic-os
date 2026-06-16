# PAIS local-business demo

A pitch-ready dashboard a local SMB owner would actually use, plus the real
agent scaffold behind it.

## Run the dashboard

```bash
cd ~/agentic_os/trappe-demo
python3 serve.py            # serves the demo + the AI-voice backend (claude -p)
# open http://localhost:8755/
```

> Use `serve.py`, not `python3 -m http.server` — the voice demo's brain needs the
> `/api/voice` backend. The dashboard/analytics/script pages are static and work
> under either.

One dashboard, three verticals — flip with the **"Demo for"** switcher (top-right)
or deep-link:

| URL | Vertical | Theme |
|-----|----------|-------|
| `/?biz=trappe-tavern` | hospitality (bar) | espresso + brass |
| `/?biz=trufit` | fitness (gym) | black + electric lime |
| `/?biz=advanced-dentistry` | personal care (dental) | slate + clinical teal |

**Try in the demo:** approve a draft (animates out, bumps the ROI counter, hits
the feed), edit a draft inline, hit "run now" on an agent, and wait ~9s for a
**live missed call** to land in the inbox.

### Files
- `index.html` · `styles.css` — structure + design system (3 themes)
- `data.js` — per-vertical datasets (the shape the runtime pushes)
- `app.js` — render + interaction
- `ARCHITECTURE.md` — how agents run on the customer's machine + talk to PAIS

## Run the real agents (scaffold)

Lives in `../local_business/`. Each agent is a `*_workflow.py` matching the
existing PAIS convention (`_running`, `is_running()`, `async def run()`),
driven by `claude -p` on the customer's own subscription.

```bash
cd ~/agentic_os
python -m local_business.runtime --business trappe-tavern --once    # run agents, write .state/<id>.json
python -m local_business.runtime --business trappe-tavern --serve   # schedule + heartbeat + approval loop
```

Configs: `local_business/businesses/{trappe-tavern,trufit,advanced-dentistry}.json`.
Integration seams (TODOs) live in `local_business/tools/{reviews,sms,contacts,pos}.py`.
