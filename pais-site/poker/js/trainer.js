// play/js/trainer.js — Scenario Trainer: auto-skips boring preflop folds,
// shows interactive spots, reveals GTO after each hero decision.

import { dealHandAsync, advisorDecision } from "../engine/simulator.js";
import { advise } from "../engine/advisor.js";
import { createLearningState } from "../engine/learning.js";
import { buildOpponents } from "./opponents.js";
import { mulberry32, randomSeed } from "./rng.js";
import { createTableView } from "./render/table.js";
import { createActionBar } from "./render/actionBar.js";
import { createHumanBridge } from "./humanProvider.js";
import {
  setMuted, getMuted,
  soundDeal, soundCardFlip, soundChip, soundFold, soundWin, soundChipSlide,
} from "./audio.js";
import { initImport } from "./handImport.js";
import {
  isCorrect, computeEvLoss, classifyMistake, checkSizing, MISTAKE_LABELS,
  emptyLeaks, recordLeak,
} from "../engine/scoring.js";

const BIG_BLIND   = 1;
const START_STACK = 100;
const SUIT_SYM    = { s: "♠", h: "♥", d: "♦", c: "♣" };

const POSITION_LABELS = {
  2: ["BTN", "BB"],
  3: ["BTN", "SB", "BB"],
  4: ["BTN", "SB", "BB", "CO"],
  5: ["BTN", "SB", "BB", "UTG", "CO"],
  6: ["BTN", "SB", "BB", "UTG", "HJ", "CO"],
  7: ["BTN", "SB", "BB", "UTG", "UTG+1", "HJ", "CO"],
  8: ["BTN", "SB", "BB", "UTG", "UTG+1", "UTG+2", "HJ", "CO"],
};

let trainedState  = null;
let tableView     = null;
let actionBar     = null;
let bridge        = null;

const human = {
  id: "human",
  name: "You",
  stack: START_STACK,
  learningState: createLearningState(),
  tendencies: {},
};

let ais = [];

const state = {
  buttonOffset: 0,
  drillCount:   0,
  correct:      0,
  total:        0,
  totalEvLoss:  0,
  evLossHands:  0,
  leaks: emptyLeaks(),
};

function defaultLearningState() {
  return { version: 1, tightness: 0.75, aggression: 0.35, handsLearned: 0, buckets: {}, notes: [] };
}

// ── Persistent stats ──────────────────────────────────────────────────────────

const STATS_KEY = "dmh-drill-stats";

function loadStats() {
  try {
    const s = JSON.parse(localStorage.getItem(STATS_KEY) || "null");
    if (s && Number.isFinite(s.correct) && Number.isFinite(s.total)) {
      state.correct      = s.correct;
      state.total        = s.total;
      state.totalEvLoss  = s.totalEvLoss  || 0;
      state.evLossHands  = s.evLossHands  || 0;
      if (s.leaks?.street) state.leaks = s.leaks;
    }
  } catch {}
}

function saveStats() {
  try {
    localStorage.setItem(STATS_KEY, JSON.stringify({
      correct:     state.correct,
      total:       state.total,
      totalEvLoss: state.totalEvLoss,
      evLossHands: state.evLossHands,
      leaks:       state.leaks,
    }));
  } catch {}
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

function getTableSize() {
  const el = document.getElementById("drillTableSize");
  return el ? parseInt(el.value, 10) : 8;
}

function showHeroCards(cards) {
  const el = document.getElementById("heroHoleCards");
  if (!el || !cards) return;
  el.innerHTML = "";
  for (const c of cards) {
    const rank = c[0] === "T" ? "10" : c[0];
    const suit = c[1];
    const red  = suit === "h" || suit === "d";
    const div  = document.createElement("div");
    div.className = "card dealing" + (red ? " card-red-suit" : "");
    div.innerHTML = `
      <div class="card-corner card-corner-tl"><span class="card-rank">${rank}</span><span class="card-csuit">${SUIT_SYM[suit] || suit}</span></div>
      <span class="card-center" aria-hidden="true">${SUIT_SYM[suit] || suit}</span>
      <div class="card-corner card-corner-br" aria-hidden="true"><span class="card-rank">${rank}</span><span class="card-csuit">${SUIT_SYM[suit] || suit}</span></div>`;
    el.appendChild(div);
    requestAnimationFrame(() => requestAnimationFrame(() => div.classList.remove("dealing")));
  }
}

function clearHeroCards() {
  const heroEl = document.getElementById("heroHoleCards");
  if (heroEl) heroEl.innerHTML = "";
}

function setWaiting(msg) {
  const el = document.getElementById("abWaiting");
  if (el) el.textContent = msg;
}

function updateDrillHeader() {
  const countEl = document.getElementById("drillHandCount");
  if (countEl) countEl.textContent = `Hand #${state.drillCount}`;
  updateAccuracy();
}

function updateAccuracy() {
  const el = document.getElementById("drillAccuracy");
  if (!el) return;
  if (state.total === 0) { el.textContent = "—"; el.className = "drill-accuracy"; return; }
  const pct = Math.round((state.correct / state.total) * 100);
  const cls = pct >= 70 ? "acc-good" : pct >= 50 ? "acc-mid" : "acc-low";
  el.className = `drill-accuracy ${cls}`;
  if (state.evLossHands > 0) {
    const avg = (state.totalEvLoss / state.evLossHands).toFixed(1);
    el.textContent = `${pct}% · −${avg}bb/h`;
  } else {
    el.textContent = `${pct}% (${state.correct}/${state.total})`;
  }
  el.title = "GTO accuracy · avg EV loss per postflop decision — click to reset";
}

// ── GTO Reveal ────────────────────────────────────────────────────────────────

function showGTOReveal(advice, userActionType, correct, evLoss, sizing = null) {
  const panel    = document.getElementById("gtoReveal");
  const verdictEl= document.getElementById("gtoVerdict");
  const detailEl = document.getElementById("gtoDetail");
  const equityEl = document.getElementById("gtoEquity");
  const evLossEl = document.getElementById("gtoEvLoss");
  if (!panel) return;

  const rec = (advice?.meta?.mix?.display || advice?.recommended?.action || "").toUpperCase();

  if (verdictEl) {
    verdictEl.textContent = correct
      ? sizing
        ? `✓ Right move — size off (GTO: ${sizing.wanted.toFixed(1)})`
        : "✓ Correct!"
      : `✗ GTO: ${rec}`;
  }
  if (detailEl) {
    const note = advice?.recommended?.note || advice?.meta?.caveat || "";
    detailEl.textContent = note ? note.slice(0, 120) : "";
  }
  if (equityEl && advice?.meta?.equity != null) {
    equityEl.textContent = `Equity ${advice.meta.equity.toFixed(0)}%`;
  } else if (equityEl) {
    equityEl.textContent = "";
  }
  if (evLossEl) {
    if (evLoss != null && evLoss > 0.05) {
      evLossEl.textContent = `−${evLoss.toFixed(1)}bb`;
      evLossEl.className = "gto-ev-loss ev-loss-bad";
    } else if (evLoss != null && evLoss <= 0.05 && correct) {
      evLossEl.textContent = "0.0bb";
      evLossEl.className = "gto-ev-loss ev-loss-ok";
    } else {
      evLossEl.textContent = "";
      evLossEl.className = "gto-ev-loss";
    }
  }

  panel.classList.toggle("gto-correct", correct);
  panel.classList.toggle("gto-wrong", !correct);
  panel.removeAttribute("hidden");
}

function hideGTOReveal() {
  const panel = document.getElementById("gtoReveal");
  if (panel) panel.setAttribute("hidden", "");
}

// ── Toast ─────────────────────────────────────────────────────────────────────

function showToast(msg, duration = 2400) {
  const container = document.getElementById("toastContainer");
  if (container) {
    const el = document.createElement("div");
    el.className = "toast";
    el.textContent = msg;
    container.appendChild(el);
    setTimeout(() => {
      el.style.transition = "opacity 300ms, transform 300ms";
      el.style.opacity    = "0";
      el.style.transform  = "translateX(-8px)";
      setTimeout(() => el.remove(), 350);
    }, duration);
    return;
  }
  const fallback = document.getElementById("toastModal");
  if (fallback) {
    fallback.textContent = msg;
    fallback.hidden = false;
    setTimeout(() => { fallback.hidden = true; }, duration);
  }
}

// ── Leak panel ────────────────────────────────────────────────────────────────

function updateLeakPanel() {
  const el = document.getElementById("leakStats");
  if (!el) return;
  const L = state.leaks;
  const rows = [];

  if (state.evLossHands > 0) {
    const avg = (state.totalEvLoss / state.evLossHands);
    const cls = avg > 1.5 ? "leak-bad" : avg > 0.5 ? "leak-mid" : "leak-ok";
    rows.push(`<div class="leak-row"><span class="leak-label">Avg EV loss/decision</span><span class="leak-val ${cls}">${avg.toFixed(2)}bb</span></div>`);
  }

  const streets = Object.entries(L.street)
    .filter(([, v]) => v.t >= 3)
    .map(([k, v]) => ({ k, pct: v.c / v.t, t: v.t }))
    .sort((a, b) => a.pct - b.pct);
  if (streets.length) {
    const w = streets[0];
    const pct = Math.round(w.pct * 100);
    rows.push(`<div class="leak-row"><span class="leak-label">Weakest street</span><span class="leak-val ${pct < 50 ? 'leak-bad' : 'leak-mid'}">${w.k} ${pct}% (${w.t})</span></div>`);
  }

  const positions = Object.entries(L.position)
    .filter(([, v]) => v.t >= 3)
    .map(([k, v]) => ({ k, pct: v.c / v.t, t: v.t }))
    .sort((a, b) => a.pct - b.pct);
  if (positions.length) {
    const w = positions[0];
    const pct = Math.round(w.pct * 100);
    rows.push(`<div class="leak-row"><span class="leak-label">Weakest position</span><span class="leak-val ${pct < 50 ? 'leak-bad' : 'leak-mid'}">${w.k} ${pct}% (${w.t})</span></div>`);
  }

  const mistakes = Object.entries(L.mistake)
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1]);
  if (mistakes.length) {
    const [type, count] = mistakes[0];
    rows.push(`<div class="leak-row"><span class="leak-label">Top mistake</span><span class="leak-val leak-bad">${MISTAKE_LABELS[type] || type} ×${count}</span></div>`);
  }

  // Full street breakdown
  const streetRows = Object.entries(L.street)
    .filter(([, v]) => v.t > 0)
    .map(([k, v]) => {
      const pct = Math.round((v.c / v.t) * 100);
      const cls = pct >= 70 ? "leak-ok" : pct >= 50 ? "leak-mid" : "leak-bad";
      return `<div class="leak-row leak-row-sm"><span class="leak-label">${k}</span><span class="leak-val ${cls}">${pct}% (${v.c}/${v.t})</span></div>`;
    }).join("");

  const posRows = Object.entries(L.position)
    .filter(([, v]) => v.t > 0)
    .sort((a, b) => (a[1].c/a[1].t) - (b[1].c/b[1].t))
    .map(([k, v]) => {
      const pct = Math.round((v.c / v.t) * 100);
      const cls = pct >= 70 ? "leak-ok" : pct >= 50 ? "leak-mid" : "leak-bad";
      return `<div class="leak-row leak-row-sm"><span class="leak-label">${k}</span><span class="leak-val ${cls}">${pct}% (${v.c}/${v.t})</span></div>`;
    }).join("");

  if (rows.length === 0 && !streetRows) {
    el.innerHTML = `<p class="leak-empty">Play more hands to see your leaks.</p>`;
    return;
  }

  el.innerHTML = `
    <div class="leak-summary">${rows.join("") || '<p class="leak-empty">Not enough data yet.</p>'}</div>
    ${streetRows ? `<div class="leak-section-label">By street</div><div class="leak-grid">${streetRows}</div>` : ""}
    ${posRows    ? `<div class="leak-section-label">By position</div><div class="leak-grid">${posRows}</div>` : ""}
  `;
}

// ── Core drill ────────────────────────────────────────────────────────────────

async function playOneDrill() {
  const TABLE_SIZE = getTableSize();
  const tableOrder = [human, ...ais.slice(0, TABLE_SIZE - 1)];
  const n          = tableOrder.length;
  const seated     = Array.from({ length: n }, (_, i) => tableOrder[(i + state.buttonOffset) % n]);
  const humanSeatIdx = seated.findIndex(a => a.id === "human");
  const agents     = seated.map(a => ({
    ...a,
    learningState: a.learningState ?? null,
    tendencies:    a.tendencies ?? {},
  }));

  const rng = mulberry32(randomSeed());
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  let handShown = false;
  let handState = null;
  const buffered = [];

  setWaiting("Finding interesting hand…");
  hideGTOReveal();

  await dealHandAsync(agents, BIG_BLIND, rng, {
    record: false,
    onEvent: (evt) => {
      if (!handShown) { buffered.push(evt); return; }
      if (evt.kind === "street") {
        soundChipSlide();
      } else if (evt.kind === "action") {
        const at = evt.action?.type;
        if (at === "raise" || at === "allin" || at === "call" || at === "blind") soundChip();
        else if (at === "fold" && handState?.seated?.[evt.seatIdx]?.id !== "human") soundFold();
      } else if (evt.kind === "showdown" && evt.pots && handState?.seated) {
        const humanIdx = handState.seated.findIndex(a => a.id === "human");
        if (humanIdx >= 0 && evt.pots.some(p => p.winners.includes(humanIdx))) soundWin();
      }
      if (tableView && handState) tableView.handleEvent(evt, handState);
    },
    actionProvider: async (decision) => {
      if (decision.seatIdx !== humanSeatIdx) {
        const delay = handShown && !reducedMotion ? 280 + Math.floor(rng() * 380) : 0;
        if (handShown && tableView && handState) {
          if (decision.snap?.pot != null) tableView.setPot(decision.snap.pot, BIG_BLIND);
          tableView.handleEvent({ kind: "turn", seatIdx: decision.seatIdx, ms: delay }, handState);
        }
        if (delay > 0) await sleep(delay);
        return advisorDecision(decision);
      }

      // ── Hero decision ──
      const advice = advise({ ...decision.snap, iterations: 400 });

      // Skip boring preflop fold spots
      if (!handShown && decision.street === "preflop" && advice.recommended?.action === "FOLD") {
        return { action: { type: "fold" } };
      }

      // ── First interesting decision: init table and replay history ──
      if (!handShown) {
        handShown = true;
        state.drillCount++;
        handState = { bigBlind: BIG_BLIND, seated: agents };

        tableView.init(n);
        tableView.positionSeats(n, humanSeatIdx);
        tableView.setDealerBtn(0);
        clearHeroCards();

        for (const evt of buffered) tableView.handleEvent(evt, handState);
        buffered.length = 0;
        soundDeal();

        tableView.setPot(decision.snap?.pot ?? 0, BIG_BLIND);

        if (decision.snap?.heroCards) {
          soundCardFlip();
          showHeroCards(decision.snap.heroCards);
          tableView.handleEvent({ kind: "hero_cards", cards: decision.snap.heroCards, seatIdx: humanSeatIdx }, handState);
        }

        updateDrillHeader();
      }

      // Update pot, turn ring, action bar
      tableView.setPot(decision.snap?.pot ?? 0, BIG_BLIND);
      hideGTOReveal();
      if (handState) tableView.handleEvent({ kind: "turn", seatIdx: humanSeatIdx, ms: 0 }, handState);
      actionBar.activate(decision, BIG_BLIND);

      const heroPos = (POSITION_LABELS[n] || [])[humanSeatIdx] || "";
      const facingEl = document.getElementById("abFacing");
      if (heroPos && facingEl) facingEl.textContent = `[${heroPos}] ${facingEl.textContent}`;
      setWaiting("");

      const input = await bridge.waitForAction(decision);
      actionBar.deactivate();
      setWaiting("Watching hand…");

      const correct     = isCorrect(advice, input.action?.type);
      const evLoss      = computeEvLoss(advice, input.action?.type);
      const sizing      = correct && /raise|allin/.test(input.action?.type || "")
        ? checkSizing(advice, input.action?.total) : null;
      const mistakeType = classifyMistake(advice, input.action?.type) || (sizing ? "sizing_off" : null);

      state.total++;
      if (correct) state.correct++;
      if (evLoss != null) {
        state.totalEvLoss += evLoss;
        state.evLossHands++;
      }
      recordLeak(state.leaks, {
        street: advice.street || decision.street,
        position: heroPos,
        correct,
        mistakeType,
      });
      saveStats();
      updateAccuracy();
      showGTOReveal(advice, input.action?.type, correct, evLoss, sizing);

      return input;
    },
  });

  for (const ai of ais) {
    if (ai.stack < BIG_BLIND) ai.stack = START_STACK;
  }
  if (human.stack < BIG_BLIND) human.stack = START_STACK;

  return handShown;
}

// ── Drill loop ────────────────────────────────────────────────────────────────

async function drillLoop() {
  while (true) {
    try {
      const shown = await playOneDrill();
      if (shown) await sleep(1400);
    } catch (err) {
      if (err?.isAbort) {
        // intentional reset
      } else {
        console.error("Drill error:", err);
        showToast("Hand error — moving to next");
        await sleep(800);
      }
    }
    state.buttonOffset = (state.buttonOffset + 1) % Math.max(2, getTableSize());
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────

async function init() {
  try {
    const r = await fetch("/poker/trained_state.json");
    const d = await r.json();
    if (!d.fallback) trainedState = d;
  } catch {}

  const TABLE_SIZE = getTableSize();
  const ls = trainedState || defaultLearningState();
  const rng = mulberry32(randomSeed());
  ais = buildOpponents(ls, 7, rng);
  ais.forEach(ai => { ai.stack = START_STACK; });

  const tableEl = document.querySelector(".table");
  tableView = createTableView(tableEl);
  bridge    = createHumanBridge();
  actionBar = createActionBar(document.getElementById("actionBar"), bridge);

  loadStats();

  const btnVolume = document.getElementById("btnDrillVolume");
  if (btnVolume) {
    btnVolume.textContent = getMuted() ? "🔇" : "🔊";
    btnVolume.addEventListener("click", () => {
      const nowMuted = !getMuted();
      setMuted(nowMuted);
      btnVolume.textContent = nowMuted ? "🔇" : "🔊";
    });
  }

  const accEl = document.getElementById("drillAccuracy");
  if (accEl) {
    accEl.addEventListener("click", () => {
      state.correct     = 0;
      state.total       = 0;
      state.totalEvLoss = 0;
      state.evLossHands = 0;
      state.leaks       = emptyLeaks();
      saveStats();
      updateAccuracy();
      updateLeakPanel();
    });
  }

  const sizeEl = document.getElementById("drillTableSize");
  if (sizeEl) {
    sizeEl.addEventListener("change", () => {
      bridge.abort();
      actionBar.deactivate();
    });
  }

  // Leak panel toggle
  const btnLeaks = document.getElementById("btnLeaks");
  const leakPanel = document.getElementById("leakPanel");
  const btnLeakClose = document.getElementById("btnLeakClose");
  if (btnLeaks && leakPanel) {
    btnLeaks.addEventListener("click", () => {
      updateLeakPanel();
      leakPanel.hidden = !leakPanel.hidden;
    });
  }
  if (btnLeakClose && leakPanel) {
    btnLeakClose.addEventListener("click", () => { leakPanel.hidden = true; });
  }

  // Import modal
  initImport({ showToast });

  const initSize = TABLE_SIZE;
  tableView.init(initSize);
  tableView.positionSeats(initSize, 0);

  updateDrillHeader();
  drillLoop();
}

init().catch(err => { console.error("Trainer init error:", err); });
