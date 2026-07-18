// play/js/handImport.js — PokerNow hand-history import.
// Parses a PokerNow CSV export, reconstructs decision points where hero's
// hole cards are known (showdown / show-then-muck hands), and runs each
// through advise() to score the session.

import { advise } from "../engine/advisor.js";
import { actionTokens, isCorrect } from "../engine/scoring.js";

// ── CSV parser ────────────────────────────────────────────────────────────────

// PokerNow CSV row: entry,order,code
// The "entry" column is double-quoted and may contain escaped quotes.
function parseCsvRows(text) {
  const rows = [];
  // Split on newlines but handle \r\n
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  for (const line of lines) {
    if (!line.trim()) continue;
    // Each line is: "entry text...",order,code
    // The entry itself is wrapped in double quotes and may contain \"
    const m = line.match(/^"((?:[^"\\]|\\.)*)"\s*,\s*(\d+)\s*,\s*(\S+)\s*$/);
    if (m) {
      rows.push({ entry: m[1].replace(/\\"/g, '"'), order: parseInt(m[2], 10), code: m[3] });
    }
  }
  return rows;
}

// ── Log line parsers ──────────────────────────────────────────────────────────

const RE_HAND_START = /-- starting hand #(\d+)/;
const RE_HAND_END   = /-- ending hand/;
const RE_SMALL_BLIND = /"(.+?)" posts a small blind of ([\d.]+)/;
const RE_BIG_BLIND   = /"(.+?)" posts a big blind of ([\d.]+)/;
const RE_RAISES      = /"(.+?)" raises to ([\d.]+)/;
const RE_CALLS       = /"(.+?)" calls ([\d.]+)/;
const RE_FOLDS       = /"(.+?)" folds/;
const RE_CHECKS      = /"(.+?)" checks/;
const RE_BETS        = /"(.+?)" bets ([\d.]+)/;
const RE_ALLIN_RAISE = /"(.+?)" raises and goes all in with ([\d.]+)/;
const RE_ALLIN_BET   = /"(.+?)" bets and goes all in with ([\d.]+)/;
const RE_ALLIN_CALL  = /"(.+?)" calls and goes all in with ([\d.]+)/;
const RE_SHOWS       = /"(.+?)" shows a \[([^\]]+)\]/;
const RE_COLLECTED   = /"(.+?)" collected ([\d.]+) from/;
const RE_STACK       = /"(.+?)" \(seat (\d+)\): ([\d.]+)/;
const RE_DEALER      = /dealer: "(.+?)"/;
const RE_FLOP        = /^Flop: \[([^\]]+)\]/;
const RE_TURN        = /^Turn: \[([^\]]+)\] \[([^\]]+)\]/;
const RE_RIVER       = /^River: \[([^\]]+)\] \[([^\]]+)\]/;
const RE_PLAYER_STACKS = /"(.+?)" \(seat \d+\)/;

function parseCard(s) {
  // PokerNow uses "Ah", "2d", "Tc" etc — match our engine's format
  const trimmed = s.trim();
  if (trimmed.length < 2) return null;
  let rank = trimmed[0].toUpperCase();
  if (rank === "1" && trimmed[1] === "0") { rank = "T"; }
  const suit = trimmed[trimmed.length - 1].toLowerCase();
  return rank + suit;
}

function parseCards(str) {
  return str.split(",").map(s => parseCard(s)).filter(Boolean);
}

// ── Group log lines by hand ───────────────────────────────────────────────────

function groupByHand(rows) {
  const hands = [];
  let current = null;
  for (const row of rows.sort((a, b) => a.order - b.order)) {
    if (RE_HAND_START.test(row.entry)) {
      if (current) hands.push(current);
      current = { lines: [] };
    }
    if (current) current.lines.push(row.entry);
    if (RE_HAND_END.test(row.entry) && current) {
      hands.push(current);
      current = null;
    }
  }
  if (current) hands.push(current);
  return hands;
}

// ── Parse a single hand ───────────────────────────────────────────────────────

function parseHand(lines, heroName) {
  const result = {
    heroCards: null,
    board: [],
    street: "preflop",
    decisions: [],   // { street, heroCards, board, state } each is a HandSnapshot
    known: false,    // true if we found hero's hole cards
  };

  let bigBlind = 1;
  let pot = 0;
  const stacks = {};        // name → stack
  const bets = {};          // name → current street bet
  const folded = new Set();
  let street = "preflop";
  let board = [];
  const preflopRoles = {};  // name → raiser/caller/3bettor/limper/blind-check
  let lastRaiser = null;
  let facingBet = 0;        // total facing amount (what the last bet/raise was to)

  for (const line of lines) {
    // Stacks from hand start block
    const smStack = line.match(/"(.+?)" \(seat \d+\): ([\d.]+)/);
    if (smStack) {
      stacks[smStack[1]] = parseFloat(smStack[2]);
    }

    const smBB = RE_BIG_BLIND.exec(line);
    if (smBB) { bigBlind = parseFloat(smBB[2]); bets[smBB[1]] = parseFloat(smBB[2]); pot += parseFloat(smBB[2]); preflopRoles[smBB[1]] = "blind-check"; }

    const smSB = RE_SMALL_BLIND.exec(line);
    if (smSB) { bets[smSB[1]] = parseFloat(smSB[2]); pot += parseFloat(smSB[2]); preflopRoles[smSB[1]] = "blind-check"; }

    // Street transitions
    const smFlop = RE_FLOP.exec(line);
    if (smFlop) {
      board = parseCards(smFlop[1]);
      street = "flop";
      Object.keys(bets).forEach(k => { bets[k] = 0; });
      facingBet = 0;
    }
    const smTurn = RE_TURN.exec(line);
    if (smTurn) {
      board = parseCards(smTurn[1] + "," + smTurn[2]);
      street = "turn";
      Object.keys(bets).forEach(k => { bets[k] = 0; });
      facingBet = 0;
    }
    const smRiver = RE_RIVER.exec(line);
    if (smRiver) {
      board = parseCards(smRiver[1] + "," + smRiver[2]);
      street = "river";
      Object.keys(bets).forEach(k => { bets[k] = 0; });
      facingBet = 0;
    }

    // Actions
    const smFold = RE_FOLDS.exec(line);
    if (smFold) { folded.add(smFold[1]); }

    const smCheck = RE_CHECKS.exec(line);
    if (smCheck && smCheck[1] === heroName) {
      // Hero checks — record if on a non-preflop street
      if (street !== "preflop" && result.heroCards) {
        result.decisions.push(buildSnap(heroName, "check", street, board, pot, 0, stacks, bets, folded, preflopRoles, bigBlind));
      }
    }

    const smCall = RE_CALLS.exec(line);
    if (smCall) {
      const name = smCall[1]; const amt = parseFloat(smCall[2]);
      pot += amt;
      bets[name] = (bets[name] || 0) + amt;
      if (street === "preflop") preflopRoles[name] = "caller";
      if (name === heroName && street !== "preflop" && result.heroCards) {
        result.decisions.push(buildSnap(heroName, "call", street, board, pot - amt, amt, stacks, bets, folded, preflopRoles, bigBlind));
      }
    }

    const smAllInCall = RE_ALLIN_CALL.exec(line);
    if (smAllInCall) {
      const name = smAllInCall[1]; const amt = parseFloat(smAllInCall[2]);
      pot += amt; bets[name] = (bets[name] || 0) + amt;
      if (name === heroName && result.heroCards) {
        result.decisions.push(buildSnap(heroName, "allin", street, board, pot - amt, amt, stacks, bets, folded, preflopRoles, bigBlind));
      }
    }

    const smBet = RE_BETS.exec(line);
    if (smBet) {
      const name = smBet[1]; const amt = parseFloat(smBet[2]);
      pot += amt; bets[name] = (bets[name] || 0) + amt; facingBet = bets[name];
      if (name === heroName && street !== "preflop" && result.heroCards) {
        result.decisions.push(buildSnap(heroName, "bet", street, board, pot - amt, 0, stacks, bets, folded, preflopRoles, bigBlind));
      }
    }

    const smRaise = RE_RAISES.exec(line);
    if (smRaise) {
      const name = smRaise[1]; const total = parseFloat(smRaise[2]);
      const prev = bets[name] || 0;
      pot += total - prev; bets[name] = total;
      if (street === "preflop") preflopRoles[name] = lastRaiser ? "3bettor" : "raiser";
      lastRaiser = name;
      if (name === heroName) {
        const toCall = total - prev;
        if (street !== "preflop" && result.heroCards) {
          result.decisions.push(buildSnap(heroName, "raise", street, board, pot - toCall, 0, stacks, bets, folded, preflopRoles, bigBlind));
        }
      }
    }

    const smAllInRaise = RE_ALLIN_RAISE.exec(line);
    if (smAllInRaise) {
      const name = smAllInRaise[1]; const total = parseFloat(smAllInRaise[2]);
      const prev = bets[name] || 0;
      pot += total - prev; bets[name] = total;
      if (name === heroName && result.heroCards) {
        result.decisions.push(buildSnap(heroName, "allin", street, board, pot - (total - prev), 0, stacks, bets, folded, preflopRoles, bigBlind));
      }
    }

    // Hero shows cards (showdown or show-muck)
    const smShows = RE_SHOWS.exec(line);
    if (smShows && smShows[1] === heroName) {
      result.heroCards = parseCards(smShows[2]);
      result.known = true;
    }
  }

  return result;
}

function buildSnap(heroName, heroAction, street, board, pot, toCall, stacks, bets, folded, preflopRoles, bigBlind) {
  // Build a minimal HandSnapshot for advise()
  const livePlayers = Object.keys(stacks).filter(n => !folded.has(n) && n !== heroName);
  const villains = livePlayers.map(name => ({
    name,
    position: "unknown",
    stack: stacks[name] || 50,
    bet: bets[name] || 0,
    preflopRole: preflopRoles[name] || "unknown",
    postflopAggressions: 0,
    postflopCalls: 0,
  }));

  return {
    heroAction,            // what hero actually did (not used by advise, just metadata)
    heroCards: null,       // filled in after showdown
    board: [...board],
    street,
    pot: Math.max(pot, 0),
    toCall: Math.max(toCall, 0),
    heroBet: bets[heroName] || 0,
    heroStack: stacks[heroName] || 50,
    heroPosition: "unknown",
    tableSize: Object.keys(stacks).length,
    villains,
    bigBlind,
  };
}

// ── Analyze a parsed hand ─────────────────────────────────────────────────────

function analyzeHand(hand) {
  if (!hand.known || !hand.heroCards || hand.decisions.length === 0) return [];
  const results = [];
  for (const snap of hand.decisions) {
    if (snap.street === "preflop") continue; // no EV data for preflop in snap mode
    const snapWithCards = { ...snap, heroCards: hand.heroCards, iterations: 300 };
    try {
      const advice = advise(snapWithCards);
      const rec = advice?.recommended?.action || "";
      const userAct = snap.heroAction;
      // Map heroAction strings to tokens
      const userType = userAct === "fold" ? "fold"
        : userAct === "call" || userAct === "check" ? userAct
        : "raise";
      const correct = isCorrect(advice, userType);
      const bestEv = advice.recommended?.ev;
      const userActObj = advice.actions?.find(a => actionTokens(a.action).has(userType));
      const evLoss = (bestEv != null && userActObj?.ev != null) ? Math.max(0, bestEv - userActObj.ev) : null;
      results.push({ street: snap.street, correct, evLoss, rec, userAction: userAct, equity: advice.meta?.equity });
    } catch { /* skip malformed spots */ }
  }
  return results;
}

// ── UI ────────────────────────────────────────────────────────────────────────

export function initImport({ showToast }) {
  const btnImport = document.getElementById("btnImport");
  const importModal = document.getElementById("importModal");
  const btnImportClose = document.getElementById("btnImportClose");
  const importFile = document.getElementById("importFile");
  const importHeroName = document.getElementById("importHeroName");
  const btnAnalyze = document.getElementById("btnAnalyze");
  const importResults = document.getElementById("importResults");

  if (!btnImport || !importModal) return;

  btnImport.addEventListener("click", () => {
    importModal.hidden = false;
  });
  btnImportClose?.addEventListener("click", () => {
    importModal.hidden = true;
  });
  importModal.addEventListener("click", (e) => {
    if (e.target === importModal) importModal.hidden = true;
  });

  btnAnalyze?.addEventListener("click", async () => {
    const file = importFile?.files?.[0];
    const heroName = (importHeroName?.value || "").trim();
    if (!file) { showToast("Choose a PokerNow CSV file first."); return; }
    if (!heroName) { showToast("Enter your player name as shown in the log."); return; }

    btnAnalyze.disabled = true;
    btnAnalyze.textContent = "Analyzing…";
    if (importResults) importResults.innerHTML = "";

    try {
      const text = await file.text();
      const rows = parseCsvRows(text);
      if (rows.length === 0) { showToast("No log entries found. Make sure this is a PokerNow CSV export."); return; }

      const hands = groupByHand(rows);
      let analyzed = 0, totalDecisions = 0, correct = 0, totalEvLoss = 0, evLossCount = 0;

      for (const hand of hands) {
        const parsed = parseHand(hand.lines, heroName);
        if (!parsed.known) continue;
        const results = analyzeHand(parsed);
        if (results.length === 0) continue;
        analyzed++;
        for (const r of results) {
          totalDecisions++;
          if (r.correct) correct++;
          if (r.evLoss != null) { totalEvLoss += r.evLoss; evLossCount++; }
        }
      }

      if (!importResults) return;

      if (analyzed === 0) {
        importResults.innerHTML = `
          <div class="import-empty">
            No analyzable hands found for "<strong>${heroName}</strong>".<br>
            Make sure the name exactly matches what appears in the log (copy-paste from the CSV).
          </div>`;
        return;
      }

      const pct = totalDecisions > 0 ? Math.round((correct / totalDecisions) * 100) : 0;
      const avgEvLoss = evLossCount > 0 ? (totalEvLoss / evLossCount).toFixed(2) : "n/a";
      const cls = pct >= 70 ? "acc-good" : pct >= 50 ? "acc-mid" : "acc-low";

      importResults.innerHTML = `
        <div class="import-summary">
          <div class="import-stat">
            <span class="import-stat-val ${cls}">${pct}%</span>
            <span class="import-stat-label">GTO accuracy</span>
          </div>
          <div class="import-stat">
            <span class="import-stat-val">${analyzed}</span>
            <span class="import-stat-label">hands analyzed</span>
          </div>
          <div class="import-stat">
            <span class="import-stat-val">${totalDecisions}</span>
            <span class="import-stat-label">decisions</span>
          </div>
          <div class="import-stat">
            <span class="import-stat-val ${avgEvLoss !== 'n/a' && parseFloat(avgEvLoss) > 1 ? 'leak-bad' : ''}">${avgEvLoss}bb</span>
            <span class="import-stat-label">avg EV loss/decision</span>
          </div>
        </div>
        <p class="import-note">Only postflop decisions in showdown hands are scored (hole cards must be visible in the export).</p>
      `;
    } catch (err) {
      console.error("Import error:", err);
      showToast("Parse error — check the console.");
    } finally {
      btnAnalyze.disabled = false;
      btnAnalyze.textContent = "Analyze";
    }
  });
}
