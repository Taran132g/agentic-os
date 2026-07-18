// play/js/render/history.js — History drawer with hand list and basic replay.

const SUIT_SYM = { s: "♠", h: "♥", d: "♦", c: "♣" };

function renderCardSm(c) {
  if (!c) return "";
  const rank = c[0] === "T" ? "10" : c[0];
  const suit = c[1];
  const sym  = SUIT_SYM[suit] || suit;
  const red  = suit === "h" || suit === "d";
  return `<span class="card-sm${red ? " card-red-suit" : ""}">${rank}${sym}</span>`;
}

function actionFamily(str) {
  const a = String(str || "").toUpperCase();
  if (a.includes("ALL-IN") || a === "ALLIN") return "all-in";
  if (a.includes("FOLD")) return "fold";
  if (a.includes("CHECK") || a.includes("CALL") || a.includes("LIMP")) return "check-call";
  return "bet-raise";
}

export function createHistoryDrawer(rootEl) {
  const drawer   = document.getElementById("historyDrawer");
  const listEl   = document.getElementById("historyList");
  const replayEl = document.getElementById("historyReplay");

  function open() {
    drawer.classList.add("drawer-open");
    drawer.setAttribute("aria-hidden", "false");
  }

  function close() {
    drawer.classList.remove("drawer-open");
    drawer.setAttribute("aria-hidden", "true");
    replayEl.hidden = true;
  }

  function isOpen() {
    return drawer.classList.contains("drawer-open");
  }

  document.getElementById("btnCloseHistory").addEventListener("click", close);

  function render(history) {
    if (!history.length) {
      listEl.innerHTML = '<div class="empty">No hands yet.</div>';
      return;
    }

    listEl.innerHTML = [...history].reverse().map((h, i) => {
      const profitBB = Number.isFinite(h.humanProfitBB) ? h.humanProfitBB : 0;
      const profit = profitBB >= 0
        ? `<span class="win">+${profitBB.toFixed(1)}</span>`
        : `<span class="lose">${profitBB.toFixed(1)}</span>`;

      // Human hole cards from this hand record
      const humanCards = h.history?.players?.[h.humanSeatIdx]?.holeCards;
      const cardsHtml = humanCards && humanCards.length
        ? `<span class="hand-cards">${humanCards.map(renderCardSm).join("")}</span>`
        : "";

      return `<div class="hand-item" data-idx="${history.length - 1 - i}">
        <span class="hand-no">Hand #${h.handNo}</span>
        ${cardsHtml}
        <span>${profit} BB</span>
      </div>`;
    }).join("");

    listEl.querySelectorAll(".hand-item").forEach(el => {
      el.addEventListener("click", () => {
        const idx = parseInt(el.dataset.idx, 10);
        showReplay(history[idx]);
      });
    });
  }

  function showReplay(record) {
    replayEl.hidden = false;
    const steps = buildSteps(record.history);
    if (!steps.length) {
      replayEl.innerHTML = '<div class="empty">No action recorded.</div>';
      return;
    }
    renderStep(steps, 0, record);
  }

  function buildSteps(history) {
    if (!history) return [];
    const steps = [];
    for (const street of (history.streets || [])) {
      for (const action of (street.actions || [])) {
        steps.push({ street: street.name, board: street.board || [], action });
      }
    }
    return steps;
  }

  function renderStep(steps, idx, record) {
    if (!steps.length) return;
    const step = steps[Math.min(idx, steps.length - 1)];
    const board = (step.board || []).map(c => renderCardSm(c)).join("") || "—";

    // Build coach review annotation if coach data is available
    let coachHtml = "";
    if (record.coach && record.coach.length > 0) {
      // Count how many human steps have occurred up to and including this one
      let humanStepsSeen = 0;
      const humanSeatIdx = record.humanSeatIdx;
      for (let si = 0; si <= Math.min(idx, steps.length - 1); si++) {
        const s = steps[si];
        const isHuman = s.action.playerName === "You" ||
          s.action.playerIdx === humanSeatIdx;
        if (isHuman) humanStepsSeen++;
      }

      const isCurrentHuman =
        step.action.playerName === "You" ||
        step.action.playerIdx === humanSeatIdx;

      if (isCurrentHuman && humanStepsSeen > 0) {
        const coachEntry = record.coach[humanStepsSeen - 1];
        if (coachEntry) {
          const recAction = coachEntry.recommended?.action || "";
          const actualType = coachEntry.actual?.type || "";
          const matched = actionFamily(actualType) === actionFamily(recAction);
          if (matched) {
            coachHtml = `<div class="coach-review-entry coach-match">✓ Matched: ${recAction}</div>`;
          } else {
            const noteStr = coachEntry.recommended?.note
              ? ` — ${coachEntry.recommended.note}`
              : "";
            coachHtml = `<div class="coach-review-entry coach-miss">✗ Did: ${actualType.toUpperCase()} — Advisor: ${recAction}${noteStr}</div>`;
          }
        }
      }
    }

    replayEl.innerHTML = `
      <div class="replay-nav">
        <button id="replayPrev"${idx === 0 ? " disabled" : ""}>◄</button>
        <span>${idx + 1} / ${steps.length}</span>
        <button id="replayNext"${idx === steps.length - 1 ? " disabled" : ""}>►</button>
      </div>
      <div class="replay-board">Board: ${board}</div>
      <div class="replay-action">${step.action.playerName || "?"}: ${step.action.label || step.action.type}</div>
      ${coachHtml}
    `;

    replayEl.querySelector("#replayPrev")?.addEventListener("click", () => {
      renderStep(steps, Math.max(0, idx - 1), record);
    });
    replayEl.querySelector("#replayNext")?.addEventListener("click", () => {
      renderStep(steps, Math.min(steps.length - 1, idx + 1), record);
    });
  }

  return { open, close, isOpen, render };
}
