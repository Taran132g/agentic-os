// js/engine/simulator.js — Full NLHE hand engine for self-play training.
//
// Deals cards, runs preflop/flop/turn/river betting (fold/check/call/raise/
// all-in with proper min-raise and re-queue after aggression), builds side
// pots, settles showdowns, and calls finishHand() on each agent's
// LearningState so dials evolve automatically.
//
// Pure Node module — no DOM, no Chrome APIs.

import { buildDeck, shuffled } from "./cards.js";
import { evaluate } from "./evaluator.js";
import { advise } from "./advisor.js";
import { styleAdjustments, pendingDecision, finishHand } from "./learning.js";

// Position labels by player count: index = seat offset from BTN
const LABELS = {
  2: ["BTN", "BB"],
  3: ["BTN", "SB", "BB"],
  4: ["BTN", "SB", "BB", "CO"],
  5: ["BTN", "SB", "BB", "UTG", "CO"],
  6: ["BTN", "SB", "BB", "UTG", "HJ", "CO"],
  7: ["BTN", "SB", "BB", "UTG", "UTG+1", "HJ", "CO"],
  8: ["BTN", "SB", "BB", "UTG", "UTG+1", "UTG+2", "HJ", "CO"],
};

// 320 iterations cuts sampling noise to ~2.6pp — needed to stay within range
// estimation error bounds so the equity floor triggers reliably.
const SIM_ITERATIONS = 320;

// ─── Action parsing ──────────────────────────────────────────────────────────

/**
 * Translate an advisor recommendation into a concrete decision.
 * `sizeHint` carries the advisor's actual sizing: postflop gives a numeric
 * chip amount ("raise TO" total), preflop gives a text like "2.5bb",
 * "2.3x the 3-bet" or "stack". Without it every raise silently became a
 * min-click, which distorted the whole sim.
 * @returns {{ type: "fold"|"check"|"call"|"raise"|"allin", total?: number }}
 */
function parseAction(str, player, toCall, currentBet, lastRaiseSize, pot, bigBlind, sizeHint = null) {
  const a = String(str || "").toUpperCase().trim();
  if (!a || a.includes("FOLD")) return { type: "fold" };
  if (a.includes("ALL-IN")) return { type: "allin" };
  if (a === "CHECK") return toCall > 0.001 ? { type: "call" } : { type: "check" };
  if (a === "CALL" || a === "LIMP") {
    return toCall < 0.001 ? { type: "check" } : { type: "call" };
  }

  if (a.includes("RAISE") || a.includes("BET")) {
    let total = null;

    // 1) Numeric chip size from the postflop EV model.
    //    BET sizes are chips to add (streetBet is 0 when checked to);
    //    RAISE sizes are already "raise to" totals — both map to `total`.
    if (Number.isFinite(sizeHint)) {
      total = a.includes("RAISE") ? sizeHint : player.streetBet + sizeHint;
    }

    // 2) Preflop sizing text: "2.5bb", "8bb", "2.3x the 3-bet", "stack".
    if (total === null && typeof sizeHint === "string") {
      const t = sizeHint.toLowerCase();
      const bbM = t.match(/(\d+\.?\d*)\s*bb/);
      const xM = t.match(/(\d+\.?\d*)\s*x/);
      if (bbM) total = parseFloat(bbM[1]) * bigBlind;
      else if (xM) total = parseFloat(xM[1]) * currentBet;
      else if (t.includes("stack")) return { type: "allin" };
    }

    // 3) Fallback: parse the action label itself.
    if (total === null) {
      const bbM = a.match(/(\d+\.?\d*)\s*BB/);
      const pctM = a.match(/(\d+\.?\d*)\s*%/);
      if (bbM) {
        total = parseFloat(bbM[1]) * bigBlind;
      } else if (pctM) {
        const frac = parseFloat(pctM[1]) / 100;
        total = a.includes("BET") ? frac * pot : currentBet + frac * pot;
      } else if (a.includes("POT")) {
        total = currentBet + pot;
      } else {
        total = currentBet + Math.max(lastRaiseSize, bigBlind); // min raise
      }
    }

    // Enforce min raise
    total = Math.max(total, currentBet + Math.max(lastRaiseSize, bigBlind));

    // If ≥ effective stack, shove
    if (total >= player.stack + player.streetBet - 0.001) return { type: "allin" };
    return { type: "raise", total };
  }

  return toCall < 0.001 ? { type: "check" } : { type: "call" };
}

// ─── Preflop situation ───────────────────────────────────────────────────────

function getPreflopFacing(players, heroIdx, bigBlind) {
  const hero = players[heroIdx];
  const others = players.filter((_, i) => i !== heroIdx);
  const maxBet = others.reduce((m, p) => Math.max(m, p.streetBet || 0), 0);
  if (maxBet > bigBlind + 0.001) {
    // "3bet" facing means HERO opened and got re-raised; a single raise in
    // front of a hero who hasn't raised is an "open". Cold-facing a re-raise
    // (or deeper) plays the 4bet+ continue range.
    const reraised = others.some(p => p.preflopRole === "3bettor");
    if (!reraised) return "open";
    return hero.preflopRole === "raiser" ? "3bet" : "4bet+";
  }
  // The SB's half-blind is a forced post, not a limp — only count real limpers.
  if (others.some(p => p.preflopRole === "limper")) return "limped";
  return "unopened";
}

// ─── Per-opponent exploit profile ────────────────────────────────────────────
// Derived from accumulated tendency stats (hands, jams, folds, raises, calls).
// Returns null when there's not enough data to trust the read.

function deriveSimProfile(stats) {
  if (!stats || stats.hands < 4) return null;
  const jamRate   = stats.jams   / stats.hands;
  const foldRate  = stats.folds  / Math.max(1, stats.actions);
  const isJammer  = jamRate  > 0.20;
  const isFolder  = foldRate > 0.55;
  const isStation = foldRate < 0.15 && stats.hands >= 8;
  return {
    label: isJammer ? "MANIAC" : isFolder ? "FOLDER" : isStation ? "STATION" : "BALANCED",
    rangeWidthMult: isJammer ? 1.5 : isFolder ? 0.75 : isStation ? 1.3  : 1.0,
    foldEquityMult: isJammer ? 0.4 : isFolder ? 1.8  : isStation ? 0.3  : 1.0,
    foldTo3bet:     isFolder ? 75  : isJammer  ? 20   : null,
    hints: [
      isJammer  ? "jams frequently — trap with monsters, call tighter" : "",
      isFolder  ? "folds too often — attack their BB, fire c-bets"     : "",
      isStation ? "calls everything — thin value only, skip bluffs"    : "",
    ].filter(Boolean),
  };
}

// ─── Advisor snapshot ────────────────────────────────────────────────────────

function buildSnap(players, heroIdx, board, street, toCall, currentBet, pot, bigBlind, rng) {
  const hero = players[heroIdx];
  return {
    heroCards: hero.holeCards,
    board: [...board],
    street,
    pot,
    toCall,
    heroBet: hero.streetBet,
    heroStack: hero.stack,
    heroPosition: hero.position,
    preflopFacing: street === "preflop" ? getPreflopFacing(players, heroIdx, bigBlind) : undefined,
    openSizeBB: currentBet > bigBlind + 0.001 ? currentBet / bigBlind : undefined,
    bigBlind,
    tableSize: players.filter(p => !p.folded).length,
    villains: players
      .filter((p, i) => i !== heroIdx && !p.folded)
      .map(p => ({
        name: p.name,
        position: p.position,
        stack: p.stack,
        bet: p.streetBet,
        preflopRole: p.preflopRole || "unknown",
        postflopAggressions: p.postflopAggressions || 0,
        postflopCalls: p.postflopCalls || 0,
        profile: hero.tendencies
          ? deriveSimProfile(hero.tendencies[p.agentId])
          : null,
      })),
    style: styleAdjustments(hero.learningState),
    iterations: SIM_ITERATIONS,
    rng
  };
}

// ─── Legal actions ────────────────────────────────────────────────────────────

const EPS = 0.001;

/**
 * Compute the set of legal actions for a player at the current decision point.
 * @returns {{
 *   canFold: boolean, canCheck: boolean, canCall: boolean,
 *   callAmount: number, canRaise: boolean, minRaiseTo: number,
 *   maxRaiseTo: number, canFullRaise: boolean,
 *   toCall: number, currentBet: number, pot: number
 * }}
 */
export function legalActions(player, toCall, currentBet, lastRaiseSize, pot, bigBlind, opts = {}) {
  const { raiseCount = 0, maxRaises = 4 } = opts;
  const callAmount = Math.min(toCall, player.stack);
  const minRaiseTo = currentBet + Math.max(lastRaiseSize, bigBlind);
  const maxRaiseTo = player.stack + player.streetBet;
  const canRaise = player.stack > callAmount + EPS && raiseCount < maxRaises;
  const canFullRaise = (player.stack + player.streetBet) >= minRaiseTo - EPS;
  return {
    canFold: true,
    canCheck: toCall < EPS,
    canCall: toCall >= EPS,
    callAmount,
    canRaise,
    minRaiseTo,
    maxRaiseTo,
    canFullRaise,
    toCall,
    currentBet,
    pot,
  };
}

// ─── Clamp action ─────────────────────────────────────────────────────────────

/**
 * Clamp an input action to something legal given the current legal-actions set.
 * @param {{ action: { type: string, total?: number }, equity?: number }} inputAction
 * @param {object} player
 * @param {object} legal  result of legalActions()
 * @returns {{ type: string, total?: number }}
 */
function clampAction(inputAction, player, legal) {
  let action = inputAction.action ?? inputAction;

  if (action.type === "raise") {
    if (!legal.canRaise) {
      // Can't raise at all — fall back to call or check
      return legal.canCall ? { type: "call" } : { type: "check" };
    }
    const total = action.total ?? legal.minRaiseTo;
    if (total >= legal.maxRaiseTo - EPS) {
      return { type: "allin" };
    }
    if (total < legal.minRaiseTo) {
      return { type: "raise", total: legal.minRaiseTo };
    }
    return { type: "raise", total };
  }

  if (action.type === "check") {
    // Facing a bet but trying to check → must call
    if (legal.canCall && !legal.canCheck) {
      return { type: "call" };
    }
    return { type: "check" };
  }

  if (action.type === "call") {
    // Nothing to call → convert to check
    if (!legal.canCall) {
      return { type: "check" };
    }
    return { type: "call" };
  }

  return action;
}

// ─── Advisor decision ─────────────────────────────────────────────────────────

/**
 * Run the advisor on a DecisionPoint yielded by the generator and return
 * a { action, equity } object suitable for feeding back into the generator.
 * @param {object} decision  DecisionPoint yielded by bettingRound generator
 * @returns {{ action: { type: string, total?: number }, equity: number|null }}
 */
export function advisorDecision(decision) {
  const { snap, legal, toCall, currentBet, lastRaiseSize, pot } = decision;
  const playerLike = { stack: snap.heroStack, streetBet: snap.heroBet };
  let action, decisionEquity = null;
  try {
    const advice = advise(snap);
    decisionEquity = advice.meta?.equity ?? null;
    const rec = advice.recommended || {};
    const recStr = rec.action || (toCall < EPS ? "CHECK" : "CALL");
    action = parseAction(recStr, playerLike, toCall, currentBet, lastRaiseSize, pot, snap.bigBlind, rec.size ?? null);
  } catch {
    action = toCall < EPS
      ? { type: "check" }
      : (toCall < pot * 0.4 ? { type: "call" } : { type: "fold" });
  }
  return { action, equity: decisionEquity };
}

// ─── Betting round (generator) ───────────────────────────────────────────────

/**
 * Generator that runs one betting street.
 * Yields a DecisionPoint for each player's decision; the caller sends back
 * { action: { type, total? }, equity: number|null }.
 * Returns the updated cumulative pot.
 *
 * Mutates players[].stack, .streetBet, .totalCommitted, .folded, .allin,
 * .preflopRole/.postflopAggressions/.postflopCalls, and .handDecisions.
 */
function* bettingRound(players, board, street, sbIdx, bbIdx, bigBlind, rng, prevPot, actionLog = null, onEvent = null) {
  const n = players.length;
  let pot = prevPot;
  let currentBet = 0;
  let lastRaiseSize = bigBlind;
  let raiseCount = 0;
  const MAX_RAISES = 4;

  for (const p of players) p.streetBet = 0;

  if (street === "preflop") {
    const sb = players[sbIdx];
    const bb = players[bbIdx];
    const sbAmt = Math.min(bigBlind / 2, sb.stack);
    const bbAmt = Math.min(bigBlind, bb.stack);
    sb.stack -= sbAmt; sb.streetBet = sbAmt;
    bb.stack -= bbAmt; bb.streetBet = bbAmt;
    if (sb.stack <= 0) sb.allin = true;
    if (bb.stack <= 0) bb.allin = true;
    pot += sbAmt + bbAmt;
    currentBet = bbAmt;
    lastRaiseSize = bbAmt;
    if (actionLog) {
      actionLog.push({ playerIdx: sbIdx, playerName: players[sbIdx].name, position: players[sbIdx].position, type: "blind", label: `SB ${(sbAmt / bigBlind).toFixed(1)}bb`, chipsDelta: sbAmt, pot: prevPot + sbAmt, stackAfter: players[sbIdx].stack, toCall: 0 });
      actionLog.push({ playerIdx: bbIdx, playerName: players[bbIdx].name, position: players[bbIdx].position, type: "blind", label: `BB ${(bbAmt / bigBlind).toFixed(1)}bb`, chipsDelta: bbAmt, pot, stackAfter: players[bbIdx].stack, toCall: 0 });
    }
    if (onEvent) {
      onEvent({ kind: "action", street: "preflop", seatIdx: sbIdx, action: { type: "blind", amount: sbAmt } });
      onEvent({ kind: "action", street: "preflop", seatIdx: bbIdx, action: { type: "blind", amount: bbAmt } });
    }
  }

  // Initial action order:
  //   preflop → start at (bbIdx+1)%n (UTG first; BTN first for HU)
  //   postflop → start at seat 1 (wraps so BTN = seat 0 acts last)
  const startIdx = street === "preflop" ? (bbIdx + 1) % n : 1;
  let actQueue = Array.from({ length: n }, (_, i) => (startIdx + i) % n);

  let safety = 0;
  while (actQueue.length > 0 && safety++ < n * 10) {
    const idx = actQueue.shift();
    const p = players[idx];
    if (p.folded || p.allin) continue;
    if (players.filter(q => !q.folded).length <= 1) break;

    const toCall = Math.max(0, currentBet - p.streetBet);

    // Yield decision point — the driver (sync or async) sends back { action, equity }
    const legal = legalActions(p, toCall, currentBet, lastRaiseSize, pot, bigBlind, { raiseCount, maxRaises: MAX_RAISES });
    const snap  = buildSnap(players, idx, board, street, toCall, currentBet, pot, bigBlind, rng);
    const input = yield { seatIdx: idx, agentId: p.agentId, street, snap, legal, toCall, currentBet, lastRaiseSize, pot, raiseCount };
    const action = clampAction(input.action, p, legal);
    const decisionEquity = input.equity ?? null;

    // Record voluntary action for learning (with real equity when available)
    {
      const label = action.type === "fold" ? "FOLD"
        : action.type === "check" ? "CHECK"
        : action.type === "call" ? "CALL"
        : action.type === "allin" ? "ALL-IN"
        : `BET ${Math.round(((action.total - currentBet) / Math.max(pot, 0.001)) * 100)}%`;
      p.handDecisions.push(pendingDecision({ street, equity: decisionEquity, actionLabel: label, pot, bigBlind }));
    }

    // Track preflop roles (feeds range estimation on later streets)
    if (street === "preflop") {
      if (action.type === "raise" || action.type === "allin") {
        const prior = players.some((q, qi) => qi !== idx && (q.preflopRole === "raiser" || q.preflopRole === "3bettor"));
        p.preflopRole = prior ? "3bettor" : "raiser";
      } else if (action.type === "call") {
        p.preflopRole = currentBet <= bigBlind + 0.001 ? "limper" : "caller";
      } else if (action.type === "check" && idx === bbIdx) {
        p.preflopRole = "blind-check";
      }
    } else {
      if (action.type === "raise" || action.type === "allin") p.postflopAggressions++;
      else if (action.type === "call") p.postflopCalls++;
    }

    const stackBefore = p.stack;

    // Apply action
    if (action.type === "fold") {
      p.folded = true;
    } else if (action.type === "check") {
      // no chips move
    } else if (action.type === "call") {
      const amt = Math.min(toCall, p.stack);
      p.stack -= amt; p.streetBet += amt; pot += amt;
      if (p.stack <= 0) p.allin = true;
    } else {
      // raise or allin
      const target = action.type === "allin" ? p.stack + p.streetBet : action.total;
      const amt = Math.min(target - p.streetBet, p.stack);
      p.stack -= amt; p.streetBet += amt; pot += amt;
      if (p.stack <= 0) p.allin = true;

      // Only re-open action if this is a genuine raise (not a short-stack call-allin)
      if (p.streetBet > currentBet + 0.001 && raiseCount < MAX_RAISES) {
        const increment = p.streetBet - currentBet;
        // A short all-in below a full raise must still be called by others,
        // but doesn't count as a raise (doesn't reset the min-raise size).
        const isFullRaise = !p.allin || increment + 0.001 >= Math.max(bigBlind, lastRaiseSize);
        if (isFullRaise) {
          lastRaiseSize = Math.max(bigBlind, increment);
          raiseCount++;
        }
        currentBet = p.streetBet;
        // Re-queue everyone active and non-allin, clockwise from raiser
        const next = [];
        for (let off = 1; off < n; off++) {
          const qi = (idx + off) % n;
          if (!players[qi].folded && !players[qi].allin) next.push(qi);
        }
        actQueue.splice(0, actQueue.length, ...next);
      }
    }

    // Determine final recorded action for log
    const finalAction = action;

    if (actionLog) {
      const chipsDelta = stackBefore - p.stack;
      let label;
      if (finalAction.type === "fold") label = "FOLD";
      else if (finalAction.type === "check") label = "CHECK";
      else if (finalAction.type === "call") label = `CALL ${(toCall / bigBlind).toFixed(1)}bb`;
      else if (finalAction.type === "allin") label = `ALL-IN (${(chipsDelta / bigBlind).toFixed(1)}bb)`;
      else label = `${toCall < 0.001 && street !== "preflop" ? "BET" : "RAISE to"} ${(p.streetBet / bigBlind).toFixed(1)}bb`;
      actionLog.push({ playerIdx: idx, playerName: p.name, position: p.position, type: finalAction.type, label, chipsDelta, pot, stackAfter: p.stack, toCall });
    }

    if (onEvent) {
      onEvent({ kind: "action", street, seatIdx: idx, action: finalAction });
    }
  }

  // Commit street bets to total hand commitment
  for (const p of players) {
    p.totalCommitted += p.streetBet;
    p.streetBet = 0;
  }

  return pot;
}

// ─── Side pots ───────────────────────────────────────────────────────────────

function buildSidePots(players) {
  const levels = [...new Set(players.map(p => p.totalCommitted))].sort((a, b) => a - b);
  const pots = [];
  let prev = 0;
  for (const level of levels) {
    if (level <= prev + 0.001) continue;
    const eligible = players.map((_, i) => i).filter(i => players[i].totalCommitted >= level - 0.001);
    const amount = (level - prev) * eligible.length;
    if (amount > 0.001) pots.push({ amount, eligible });
    prev = level;
  }
  return pots;
}

/**
 * Award pots to winners, mutating stacks.
 * Returns an array of settlement records.
 * @returns {{ amount: number, eligible: number[], winners: number[], share: number }[]}
 */
function awardPots(players, board5) {
  const settlements = [];
  for (const { amount, eligible } of buildSidePots(players)) {
    const contestants = eligible.map(i => players[i]).filter(p => !p.folded);
    if (contestants.length === 0) {
      const fallback = players.find(p => !p.folded);
      if (fallback) fallback.stack += amount;
      settlements.push({ amount, eligible, winners: fallback ? [players.indexOf(fallback)] : [], share: amount });
      continue;
    }
    if (contestants.length === 1) {
      contestants[0].stack += amount;
      settlements.push({ amount, eligible, winners: [players.indexOf(contestants[0])], share: amount });
      continue;
    }
    const scored = contestants.map(p => ({ p, score: evaluate([...p.holeCards, ...board5]).score }));
    const best = Math.max(...scored.map(s => s.score));
    const winners = scored.filter(s => s.score === best).map(s => s.p);
    const share = amount / winners.length;
    for (const w of winners) w.stack += share;
    settlements.push({ amount, eligible, winners: winners.map(w => players.indexOf(w)), share });
  }
  return settlements;
}

// ─── Core hand generator ──────────────────────────────────────────────────────

/**
 * Generator that drives one complete NLHE hand.
 * Yields DecisionPoints from bettingRound; caller must send back { action, equity }.
 * Returns the result object.
 */
function* dealHandCore(agents, bigBlind, rng, opts) {
  const n = agents.length;
  if (n < 2 || n > 8) throw new Error(`dealHand: need 2–8 agents, got ${n}`);

  const labels = LABELS[n];
  const { record = false, onEvent = null } = opts;
  const startStacks = agents.map(a => a.stack);
  const deck = shuffled(buildDeck(), rng);
  const streetHistories = [];
  function makeStreetLog(name, board) {
    if (!record) return null;
    const entry = { name, board: [...board], actions: [] };
    streetHistories.push(entry);
    return entry.actions;
  }
  const comBase = n * 2; // first community card index

  // Pre-deal all five community cards (burn cards sit at comBase+0, +4, +6)
  const board5 = [
    deck[comBase + 1], deck[comBase + 2], deck[comBase + 3], // flop
    deck[comBase + 5],                                         // turn
    deck[comBase + 7],                                         // river
  ];

  const players = agents.map((ag, i) => ({
    agentIdx: i,
    agentId: ag.id ?? i,
    name: ag.name,
    holeCards: [deck[i * 2], deck[i * 2 + 1]],
    stack: ag.stack,
    streetBet: 0,
    totalCommitted: 0,
    folded: false,
    allin: false,
    position: labels[i],
    preflopRole: null,
    postflopAggressions: 0,
    postflopCalls: 0,
    handDecisions: [],
    learningState: ag.learningState,
    tendencies: ag.tendencies ?? null,
  }));

  // Heads-up: seat 0 is BTN and SB; seat 1 is BB.
  // 3+ players: seat 1 is SB, seat 2 is BB.
  const sbIdx = n === 2 ? 0 : 1;
  const bbIdx = n === 2 ? 1 : 2;

  const active = () => players.filter(p => !p.folded);
  const canBet = () => active().length > 1 && active().some(p => !p.allin);

  if (onEvent) onEvent({ kind: "hand_start", players: players.map(p => ({ name: p.name, position: p.position, stack: p.stack })) });

  // Run betting streets
  let pot = yield* bettingRound(players, [], "preflop", sbIdx, bbIdx, bigBlind, rng, 0, makeStreetLog("preflop", []), onEvent);

  const board = [];
  if (active().length > 1) {
    board.push(board5[0], board5[1], board5[2]);
    if (onEvent) onEvent({ kind: "street", street: "flop", board: [...board] });
    const flopLog = makeStreetLog("flop", board);
    if (canBet()) pot = yield* bettingRound(players, board, "flop", sbIdx, bbIdx, bigBlind, rng, pot, flopLog, onEvent);
  }
  if (active().length > 1) {
    board.push(board5[3]);
    if (onEvent) onEvent({ kind: "street", street: "turn", board: [...board] });
    const turnLog = makeStreetLog("turn", board);
    if (canBet()) pot = yield* bettingRound(players, board, "turn", sbIdx, bbIdx, bigBlind, rng, pot, turnLog, onEvent);
  }
  if (active().length > 1) {
    board.push(board5[4]);
    if (onEvent) onEvent({ kind: "street", street: "river", board: [...board] });
    const riverLog = makeStreetLog("river", board);
    if (canBet()) pot = yield* bettingRound(players, board, "river", sbIdx, bbIdx, bigBlind, rng, pot, riverLog, onEvent);
  }

  // Showdown — always evaluate against the full 5-card board
  const settlementRecords = awardPots(players, board5);

  const profits = players.map((p, i) => p.stack - startStacks[i]);

  if (onEvent) {
    onEvent({ kind: "showdown", summary: players.map((p, i) => ({ name: p.name, cards: p.holeCards, folded: p.folded, profit: profits[i] })), pots: settlementRecords });
  }

  // Settle learning for every agent
  for (let i = 0; i < players.length; i++) {
    if (agents[i].learningState && players[i].handDecisions.length > 0) {
      finishHand(agents[i].learningState, players[i].handDecisions, profits[i] / bigBlind);
    }
  }

  const result = {
    profits,
    board: board5,
    summary: players.map((p, i) => ({
      name: p.name,
      cards: p.holeCards,
      folded: p.folded,
      profit: profits[i]
    })),
    pots: settlementRecords,
  };

  if (record) {
    result.history = {
      players: players.map((p, i) => ({
        name: p.name,
        position: p.position,
        holeCards: p.holeCards,
        startStack: startStacks[i],
        profit: profits[i],
        folded: p.folded,
      })),
      board5,
      streets: streetHistories,
    };
  }

  if (onEvent) onEvent({ kind: "hand_end", result });

  return result;
}

// ─── Public API ──────────────────────────────────────────────────────────────

/**
 * Deal and settle one complete NLHE hand between agents arranged in seat order.
 *
 *   agents[0] = BTN (dealer)
 *   agents[1] = SB  (or BB in heads-up)
 *   agents[2] = BB  (three-or-more players)
 *   ...
 *
 * Each agent: { name: string, stack: number, learningState: object }
 *
 * After the hand, each agent's learningState is updated in place via
 * finishHand() — dial nudges fire automatically.
 *
 * Returns:
 *   profits  — chips won/lost per agent; sums to ≈0
 *   board    — the five community cards
 *   summary  — [{name, cards, folded, profit}]
 *   pots     — settlement records [{amount, eligible, winners, share}]
 */
export function dealHand(agents, bigBlind = 1, rng = Math.random, opts = {}) {
  const gen = dealHandCore(agents, bigBlind, rng, opts);
  let step = gen.next();
  while (!step.done) step = gen.next(advisorDecision(step.value));
  return step.value;
}

/**
 * Async variant of dealHand.  Accepts an optional `opts.actionProvider` that
 * receives each DecisionPoint and must return (or resolve to) { action, equity }.
 * Defaults to advisorDecision — making AI-only hands byte-identical to dealHand.
 */
export async function dealHandAsync(agents, bigBlind = 1, rng = Math.random, opts = {}) {
  const provider = opts.actionProvider ?? advisorDecision;
  const gen = dealHandCore(agents, bigBlind, rng, opts);
  let step = gen.next();
  while (!step.done) {
    const input = await Promise.resolve(provider(step.value));
    step = gen.next(input);
  }
  return step.value;
}

/**
 * Run a multi-hand session with button rotation.
 * Each agent's learningState is updated in place after each hand.
 *
 * @param {{ name: string, stack: number, learningState: object }[]} agents
 * @param {number} numHands
 * @param {{ bigBlind?: number, resetStacks?: boolean, rng?: () => number }} opts
 * @returns {{ totalProfits: number[], handsPlayed: number, errors: number }}
 */
export function runSession(agents, numHands, opts = {}) {
  const { bigBlind = 1, resetStacks = true, rng = Math.random } = opts;
  const startStack = agents[0].stack;
  const totalProfits = new Array(agents.length).fill(0);
  let errors = 0;

  for (let h = 0; h < numHands; h++) {
    if (resetStacks) {
      for (const ag of agents) ag.stack = startStack;
    }
    // Rotate seats: agent who was SB becomes BTN next hand, etc.
    const rot = h % agents.length;
    const seated = [...agents.slice(rot), ...agents.slice(0, rot)];
    try {
      const result = dealHand(seated, bigBlind, rng);
      for (let i = 0; i < seated.length; i++) {
        totalProfits[agents.indexOf(seated[i])] += result.profits[i];
        if (!resetStacks) seated[i].stack += result.profits[i];
      }
    } catch {
      errors++;
    }
  }

  return { totalProfits, handsPlayed: numHands, errors };
}
