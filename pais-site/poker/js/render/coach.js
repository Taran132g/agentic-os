// play/js/render/coach.js — Coach drawer content manager.

export function createCoachPanel(rootEl) {
  const recEl        = document.getElementById("coachRec");
  const noteEl       = document.getElementById("coachNote");
  const eqEl         = document.getElementById("coachEquity");
  const actionsEl    = document.getElementById("coachActions");
  const readEl       = document.getElementById("coachRead");
  const learnSection = document.getElementById("coachLearnSection");
  const learnDialsEl = document.getElementById("coachLearnDials");
  const learnNotesEl = document.getElementById("coachLearnNotes");

  function show(advice) {
    if (!advice) {
      if (recEl) recEl.textContent = "";
      if (noteEl) noteEl.textContent = "";
      if (eqEl) eqEl.textContent = "";
      if (actionsEl) actionsEl.innerHTML = "";
      return;
    }
    const rec = advice.recommended;

    const sizeStr = rec?.size && rec.size !== "—" ? ` to ${rec.size}` : "";
    if (recEl) recEl.textContent = (rec?.action || "—") + sizeStr;
    if (noteEl) noteEl.textContent = rec?.note || "";

    if (eqEl) {
      if (advice.meta?.equity != null) {
        const pct = advice.meta.equity.toFixed(1);
        eqEl.innerHTML = `<span class="eq-label">Equity</span>
          <span class="eq-bar-wrap"><span class="eq-bar" style="width:${Math.min(100, advice.meta.equity).toFixed(0)}%"></span></span>
          <span class="eq-pct">${pct}%</span>`;
      } else {
        eqEl.textContent = "";
      }
    }

    if (actionsEl) {
      if (advice.actions && advice.actions.length > 1) {
        actionsEl.innerHTML = advice.actions.slice(0, 4).map(a => {
          const ev = a.ev != null ? ` (${a.ev >= 0 ? "+" : ""}${a.ev.toFixed(1)}bb)` : "";
          const isRec = a.action === rec?.action;
          return `<div class="coach-action-row${isRec ? " coach-action-best" : ""}">
            <span class="coa-action">${a.action}</span><span class="coa-ev">${ev}</span>
          </div>`;
        }).join("");
      } else {
        actionsEl.innerHTML = "";
      }
    }
  }

  function showRead(label) {
    if (readEl) readEl.textContent = label ? `AIs read you as: ${label}` : "";
  }

  function showReads(reads) {
    if (!readEl) return;
    if (!reads || !reads.length) { readEl.innerHTML = ""; return; }
    readEl.innerHTML = reads.map(r =>
      `<div class="coach-opp-read"><span class="read-name">${r.name}</span>` +
      `<span class="read-label read-${r.label.toLowerCase()}">${r.label}</span>` +
      `<span class="read-hands">${r.hands}h</span></div>`
    ).join("");
  }

  function showLearning(state) {
    if (!learnSection) return;
    if (!state) return;

    if (learnDialsEl) {
      const hands = state.handsLearned || 0;
      const t = Math.round(state.tightness * 100);
      const a = Math.round(state.aggression * 100);
      learnDialsEl.innerHTML =
        `<span class="learn-dial">Tight: ${t}%</span>` +
        `<span class="learn-dial">Aggro: ${a}%</span>` +
        `<span class="learn-hands">${hands} hand${hands === 1 ? "" : "s"}</span>`;
    }

    if (learnNotesEl) {
      if (state.notes?.length) {
        learnNotesEl.innerHTML = state.notes.slice(-5).map(n =>
          `<div class="coach-learn-note">${n}</div>`
        ).join("");
      } else {
        learnNotesEl.textContent = "Play more hands for personalized insights.";
      }
    }
  }

  function hide() {}

  function setMode(mode) {
    if (mode === "off") {
      if (recEl) recEl.textContent = "";
      if (noteEl) noteEl.textContent = "";
      if (eqEl) eqEl.textContent = "";
      if (actionsEl) actionsEl.innerHTML = "";
    }
  }

  return { show, hide, setMode, showRead, showReads, showLearning };
}
