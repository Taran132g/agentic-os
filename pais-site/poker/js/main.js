// play/js/main.js — Bootstrap. Wires session, engine events, and UI components.

import { createSession, runHand } from "./session.js";
import { createHumanBridge }      from "./humanProvider.js";
import { createTableView }        from "./render/table.js";
import { createActionBar }        from "./render/actionBar.js";
import { createCoachPanel }       from "./render/coach.js";
import { createHistoryDrawer }    from "./render/history.js";
import { createGameLog }          from "./render/gameLog.js";
import { loadSession, saveSession, loadHistory, clearAll } from "./storage.js";
import { buildOpponents }         from "./opponents.js";
import { mulberry32, randomSeed } from "./rng.js";
import {
  soundDeal, soundCardFlip, soundChip, soundFold, soundWin,
  soundDrawer, soundChipSlide, setMuted, getMuted,
} from "./audio.js";

let session     = null;
let bridge      = null;
let tableView   = null;
let actionBar   = null;
let coachPanel  = null;
let histDrawer  = null;
let gameLog     = null;
let handRunning = false;
let trainedState = null;

let handState = null; // { bigBlind, seated, pot }

function defaultLearningState() {
  return { version: 1, tightness: 0.58, aggression: 0.88, handsLearned: 0, buckets: {}, notes: [] };
}

// ── Toast stack ──────────────────────────────────────────────────────────────

const toastContainer = document.getElementById("toastContainer");

function showToast(msg, duration = 2600) {
  if (!toastContainer) {
    // fallback: legacy single-element toast
    const el = document.getElementById("toastModal");
    if (el) { el.textContent = msg; el.hidden = false; setTimeout(() => { el.hidden = true; }, duration); }
    return;
  }
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = msg;
  toastContainer.appendChild(el);
  setTimeout(() => {
    el.style.transition = "opacity 300ms, transform 300ms";
    el.style.opacity    = "0";
    el.style.transform  = "translateX(-8px)";
    setTimeout(() => el.remove(), 350);
  }, duration);
}

// ── Volume toggle ────────────────────────────────────────────────────────────

const btnVolume = document.getElementById("btnVolume");
if (btnVolume) {
  btnVolume.addEventListener("click", () => {
    const nowMuted = !getMuted();
    setMuted(nowMuted);
    btnVolume.textContent = nowMuted ? "🔇" : "🔊";
    btnVolume.classList.toggle("muted", nowMuted);
  });
}

// ── Drawer helpers ───────────────────────────────────────────────────────────

const coachDrawerEl = document.getElementById("coachDrawer");
const scrimEl       = document.getElementById("drawerScrim");

function openCoach() {
  coachDrawerEl?.classList.add("drawer-open");
  coachDrawerEl?.setAttribute("aria-hidden", "false");
  // No scrim for coach — it sits on the right and doesn't block game play
  soundDrawer();
}

function closeCoach() {
  coachDrawerEl?.classList.remove("drawer-open");
  coachDrawerEl?.setAttribute("aria-hidden", "true");
}

function histDrawerOpen() {
  return document.getElementById("historyDrawer")?.classList.contains("drawer-open");
}

function openHistory() {
  histDrawer.render(loadHistory());
  histDrawer.open();
  scrimEl?.removeAttribute("hidden");
  soundDrawer();
}

function closeHistory() {
  histDrawer.close();
  if (!coachDrawerEl?.classList.contains("drawer-open")) scrimEl?.setAttribute("hidden", "");
}

// Scrim click closes whatever drawer is open
if (scrimEl) {
  scrimEl.addEventListener("click", () => { closeCoach(); closeHistory(); });
}

// Esc closes drawers
document.addEventListener("keydown", e => {
  if (e.key === "Escape") { closeCoach(); closeHistory(); }
});

// ── Init ─────────────────────────────────────────────────────────────────────

async function init() {
  try {
    const r = await fetch("/poker/trained_state.json");
    const d = await r.json();
    if (!d.fallback) trainedState = d;
  } catch {}

  session = loadSession();
  if (
    !session ||
    session.version !== 1 ||
    !Array.isArray(session.ais) ||
    !session.human ||
    !session.config ||
    session.ais.length !== (session.config.tableSize ?? 4) - 1
  ) {
    session = createSession({}, trainedState);
  }

  const tableEl = document.querySelector(".table");
  tableView  = createTableView(tableEl);
  bridge     = createHumanBridge();
  actionBar  = createActionBar(document.getElementById("actionBar"), bridge);
  coachPanel = createCoachPanel(document.getElementById("coachDrawer"));
  histDrawer = createHistoryDrawer(document.getElementById("historyDrawer"));
  gameLog    = createGameLog(document.getElementById("gameLogWrap"));

  // Intercept bridge to activate action bar on human turn
  const originalWait = bridge.waitForAction.bind(bridge);
  bridge.waitForAction = function (decision) {
    actionBar.activate(decision, session.config.bigBlind);
    return originalWait(decision);
  };

  // Table size selector
  const tableSizeEl = document.getElementById("tableSize");
  tableSizeEl.value = String(session.config.tableSize);
  tableSizeEl.addEventListener("change", () => {
    const newSize = parseInt(tableSizeEl.value, 10);
    if (newSize === session.config.tableSize) return;
    session.config.tableSize = newSize;
    const rng = mulberry32(randomSeed());
    session.ais = buildOpponents(trainedState || defaultLearningState(), newSize - 1, rng);
    session.ais.forEach(ai => { ai.stack = session.config.startStack; });
    session.buttonOffset = 0;
    saveSession(session);
    bridge.abort();
    actionBar.deactivate();
  });

  // Coach mode selector (inside drawer)
  const coachModeEl = document.getElementById("coachMode");
  coachModeEl.value = session.config.coachMode;
  coachPanel.setMode(session.config.coachMode);
  coachModeEl.addEventListener("change", () => {
    session.config.coachMode = coachModeEl.value;
    coachPanel.setMode(session.config.coachMode);
    saveSession(session);
  });

  // Coach drawer toggle
  document.getElementById("btnCoach")?.addEventListener("click", () => {
    if (coachDrawerEl?.classList.contains("drawer-open")) closeCoach(); else openCoach();
  });
  document.getElementById("btnCloseCoach")?.addEventListener("click", closeCoach);

  // History drawer toggle
  document.getElementById("btnHistory")?.addEventListener("click", () => {
    if (histDrawerOpen()) closeHistory(); else openHistory();
  });

  // New session button
  document.getElementById("btnNewSession")?.addEventListener("click", () => {
    if (!confirm("Start a new session? Current stacks will be reset.")) return;
    bridge.abort();
    actionBar.deactivate();
    clearAll();
    session = createSession({ tableSize: session.config.tableSize, coachMode: session.config.coachMode }, trainedState);
    saveSession(session);
    tableSizeEl.value = String(session.config.tableSize);
    gameLog?.clear();
    updateSessionStats();
  });

  updateSessionStats();
  startHandLoop();
}

// ── Session stats ─────────────────────────────────────────────────────────────

function updateSessionStats() {
  const series = session.profitSeries;
  const total  = series[series.length - 1] || 0;
  const profitEl = document.getElementById("sessionProfit");
  profitEl.textContent = `${total >= 0 ? "+" : ""}${total.toFixed(1)} BB`;
  profitEl.style.color = total >= 0 ? "var(--win)" : "var(--lose)";
  updateSparkline(series);
  if (coachPanel && session.human.learningState) coachPanel.showLearning(session.human.learningState);
}

function updateSparkline(series) {
  const svg = document.getElementById("profitSpark");
  if (!series.length) { svg.innerHTML = ""; return; }
  const w = 120, h = 32;
  const slice = series.slice(-50);
  const min   = Math.min(0, ...slice);
  const max   = Math.max(0, ...slice);
  const range = max - min || 1;
  const pts = slice.map((v, i, arr) => {
    const x = (i / Math.max(1, arr.length - 1)) * w;
    const y = h - ((v - min) / range) * h;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const lastVal = slice[slice.length - 1] || 0;
  const color   = lastVal >= 0 ? "var(--win)" : "var(--lose)";
  const cls     = lastVal < 0 ? ' class="losing"' : "";
  svg.innerHTML = `<polyline${cls} points="${pts}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>`;
}

// ── Hand loop ─────────────────────────────────────────────────────────────────

const SUIT_SYM = { s: "♠", h: "♥", d: "♦", c: "♣" };

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
  const el = document.getElementById("heroHoleCards");
  if (el) el.innerHTML = "";
}

async function startHandLoop() {
  if (handRunning) return;
  handRunning = true;
  while (true) {
    try {
      await playOneHand();
    } catch (err) {
      if (err?.isAbort) {
        // intentional abort — restart quietly
      } else {
        const msg = err?.message || String(err);
        console.error("Hand error:", err);
        showToast(`Hand error: ${msg}`, 5000);
        await new Promise(r => setTimeout(r, 1200));
      }
    }
    saveSession(session);
    updateSessionStats();
    await new Promise(r => setTimeout(r, 600));
  }
}

async function playOneHand() {
  const n = session.config.tableSize;

  // Pre-compute seated order so human's seat index is known before init
  const tableOrder = [session.human, ...session.ais];
  const preSeated  = Array.from({ length: n }, (_, i) => tableOrder[(i + session.buttonOffset) % n]);
  const preHumanIdx = preSeated.findIndex(a => a.id === "human");

  tableView.init(n);
  tableView.positionSeats(n, preHumanIdx);
  clearHeroCards();

  handState = { bigBlind: session.config.bigBlind, seated: preSeated, pot: 0 };

  function onEvent(evt) {
    if (evt.kind === "hero_cards") {
      soundCardFlip();
      if (handState) tableView.handleEvent(evt, handState);
      showHeroCards(evt.cards);
      return;
    }

    if (evt.kind === "coach") {
      if (session.config.coachMode === "live") coachPanel.show(evt.advice);
      return;
    }

    if (evt.kind === "rebuy") {
      showToast(`${evt.name} re-buys.`);
      return;
    }

    // Feed game log
    gameLog?.onEvent(evt, handState);

    // Sounds
    if (evt.kind === "hand_start") {
      soundDeal();
    } else if (evt.kind === "street") {
      soundChipSlide();
    } else if (evt.kind === "action") {
      const at = evt.action?.type;
      if (at === "raise" || at === "allin" || at === "call" || at === "blind") soundChip();
      else if (at === "fold") {
        const isHuman = handState?.seated?.[evt.seatIdx]?.id === "human";
        if (!isHuman) soundFold();
      }
    } else if (evt.kind === "showdown" && evt.pots && handState?.seated) {
      const humanIdx = handState.seated.findIndex(a => a.id === "human");
      if (humanIdx >= 0 && evt.pots.some(p => p.winners.includes(humanIdx))) soundWin();
    }

    if (tableView && handState) tableView.handleEvent(evt, handState);
  }

  const { result, handRecord, humanSeatIdx, seated } = await runHand(session, bridge, onEvent);

  tableView.setDealerBtn(0);
  tableView.stopAllTimers();

  handState = { bigBlind: session.config.bigBlind, seated, pot: 0 };

  for (let i = 0; i < seated.length; i++) {
    const p      = result.history?.players?.[i];
    const isHuman = seated[i].id === "human";
    const holeCards = p?.holeCards || null;
    tableView.updateSeat(i, {
      name:       seated[i].name,
      position:   p?.position || "",
      stack:      seated[i].stack,
      cards:      isHuman ? holeCards : (p?.folded ? null : holeCards || ["?", "?"]),
      animate:    false,
      folded:     p?.folded  ?? false,
      allin:      p?.allin   ?? false,
      currentBet: 0,
    });
    if (isHuman && holeCards) showHeroCards(holeCards);
  }

  if (result.pots) {
    for (const pot of result.pots) {
      for (const wi of pot.winners) tableView.showWinner(wi);
    }
  }

  const totalPot = result.pots?.reduce((s, p) => s + p.amount, 0) || 0;
  tableView.setPot(totalPot, session.config.bigBlind);

  // Floating profit for hero
  const humanIdx = seated.findIndex(a => a.id === "human");
  if (humanIdx >= 0 && result.profits) {
    const profitBB = result.profits[humanIdx] / session.config.bigBlind;
    if (Math.abs(profitBB) > 0.01) tableView.showFloatingProfit(humanIdx, profitBB);
  }

  if (session.config.coachMode !== "off") updateCoachRead(seated, humanSeatIdx);
  updateSessionStats();
}

function updateCoachRead(seated, humanSeatIdx) {
  const human = seated.find(a => a.id === "human");
  const reads = [];
  for (const agent of seated) {
    if (agent.id === "human") continue;
    const t = human?.tendencies?.[agent.id];
    if (!t || (t.hands ?? 0) < 4) continue;
    const foldRate = t.folds / Math.max(1, t.actions);
    const jamRate  = t.jams  / t.hands;
    let label = "BALANCED";
    if      (jamRate  > 0.20) label = "MANIAC";
    else if (foldRate > 0.55) label = "FOLDER";
    else if (foldRate < 0.15 && t.hands >= 8) label = "STATION";
    reads.push({ name: agent.name, label, hands: t.hands });
  }
  coachPanel.showReads(reads);
}

init().catch(err => { console.error("Init error:", err); });
