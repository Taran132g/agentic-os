// Orchestrator: turns a normalized hand snapshot into one recommendation.
// Ties together positions, preflop charts, villain range estimation,
// opponent profiles and the postflop EV model.

import { parsedCharts, topPercentRange, rangePercent, chenScore, comboize } from "./ranges.js";
import { handName, RANK_VALUE } from "./cards.js";
import { chartKeyFor } from "./positions.js";
import { preflopAdvice } from "./preflop.js";
import { postflopAdvice } from "./postflop.js";
import { exploitPlan } from "./exploit.js";
import { evaluate, CATEGORY } from "./evaluator.js";

// A full hand-category step in the evaluator's packed score — used to boost
// draw combos so semi-bluffs stay inside estimated raising ranges.
const CATEGORY_STEP = 1 << 20;

/**
 * Postflop raising ranges are made of BOARD-CONNECTED hands, not preflop
 * premiums. Rank every combo in the villain's preflop-shaped range by its
 * made-hand strength on this board (plus draw bonuses), then keep the top
 * fraction. Contracting by global preflop strength instead (the old model)
 * made a raiser on 6-7-9 look like "QQ+/AK" — flattering an overpair's
 * equity while the real raising range (sets, two pair, straights) crushed it.
 * @returns {string[][]} concrete combos, strongest-on-this-board first
 */
function boardWeightedCombos(rangeSet, board, keepFrac, dead) {
  const combos = comboize(rangeSet, dead);
  if (combos.length === 0) return combos;
  const boardSuits = {};
  for (const b of board) boardSuits[b[1]] = (boardSuits[b[1]] || 0) + 1;

  const scored = combos.map(c => {
    const made = evaluate([...c, ...board]);
    let score = made.score;
    if (board.length < 5) {
      // Flush draw: suited combo with two+ of that suit on board.
      if (c[0][1] === c[1][1] && (boardSuits[c[0][1]] || 0) >= 2 && made.category < CATEGORY.FLUSH) {
        score += CATEGORY_STEP;
      }
      // Straight-draw proxy: 4 ranks within a 5-wide window using ≥1 hole card.
      const holeVals = c.map(x => RANK_VALUE[x[0]]);
      const all = [...new Set([...holeVals, ...board.map(x => RANK_VALUE[x[0]])])];
      for (let lo = 2; lo <= 10; lo++) {
        const inWindow = all.filter(v => v >= lo && v <= lo + 4);
        if (inWindow.length >= 4 && inWindow.some(v => holeVals.includes(v))) {
          score += 0.8 * CATEGORY_STEP;
          break;
        }
      }
    }
    return { c, score };
  });

  scored.sort((a, b) => b.score - a.score);
  const keep = Math.max(4, Math.round(scored.length * keepFrac));
  return scored.slice(0, keep).map(s => s.c);
}

/**
 * Estimate a villain's current range from their preflop role, the actions
 * they've taken this hand, and their long-run profile.
 *
 * @param {object} v
 * @param {string} v.position
 * @param {"raiser"|"caller"|"3bettor"|"blind-check"|"limper"|"unknown"} v.preflopRole
 * @param {number} v.postflopAggressions bets/raises this hand postflop
 * @param {number} v.postflopCalls calls this hand postflop
 * @param {object} [v.profile] deriveProfile() output
 * @returns {{range: Set<string>, pct: number, basis: string}}
 */
export function estimateVillainRange(v, board = [], pot = 0) {
  const charts = parsedCharts();
  let pct;
  let basis;

  switch (v.preflopRole) {
    case "raiser": {
      const key = chartKeyFor(v.position || "CO");
      pct = rangePercent(charts.rfi[key] || charts.rfi.CO);
      basis = `${key} open range`;
      break;
    }
    case "3bettor":
      pct = 8;
      basis = "3-bet range";
      break;
    case "caller":
      pct = rangePercent(charts.vsRfi.call);
      basis = "cold-call range";
      break;
    case "limper":
      pct = 35;
      basis = "limp range";
      break;
    case "blind-check":
      pct = 80;
      basis = "blind check — nearly any two";
      break;
    default:
      pct = 60;
      basis = "unknown preflop line";
  }

  // Long-run looseness widens/narrows everything they do.
  if (v.profile && v.profile.rangeWidthMult) {
    pct *= v.profile.rangeWidthMult;
  }
  pct = Math.min(100, Math.max(2, pct));
  const baseRange = topPercentRange(pct);

  // Postflop contraction: how much of the preflop-shaped range survives
  // this villain's aggression. Paired boards contract harder (aggression
  // there is boats/trips, not semi-bluffs); overbets contract beyond what
  // raise-count captures (`pot` includes the live bet).
  const isPaired = board.length >= 3 &&
    new Set(board.map(c => c[0])).size < board.length;
  const aggrMult = isPaired ? 0.35 : 0.55;
  let keepFrac = Math.pow(aggrMult, v.postflopAggressions || 0)
               * Math.pow(0.80, v.postflopCalls || 0);
  if (v.bet > 0 && pot > v.bet) {
    const sizeFrac = v.bet / (pot - v.bet);
    if (sizeFrac >= 1.2) keepFrac *= 0.45;
    else if (sizeFrac >= 0.75) keepFrac *= 0.75;
  }
  // Monotone board: postflop aggression signals flush-heavy range — tighten.
  if ((v.postflopAggressions || 0) >= 1 && board.length >= 3) {
    const bsc = {};
    for (const c of board) { bsc[c[1]] = (bsc[c[1]] || 0) + 1; }
    if (Math.max(0, ...Object.values(bsc)) >= 3) keepFrac *= 0.55;
  }
  // Connected board: on boards where all 3+ ranks span ≤4, aggression signals
  // straights, sets, and two pairs. Tighten so board-weighted combos weight
  // toward those made hands, which crushes overpairs in range estimation.
  if ((v.postflopAggressions || 0) >= 1 && board.length >= 3) {
    const bv = board.map(c => RANK_VALUE[c[0]]).sort((a, b) => a - b);
    const span = bv[bv.length - 1] - bv[0];
    if (span <= 2) keepFrac *= 0.40;        // e.g. 5-6-7 — extremely connected
    else if (span <= 4) keepFrac *= 0.55;   // e.g. 5-6-9 — moderately connected
  }

  if (board.length >= 3 && keepFrac < 1) {
    const combos = boardWeightedCombos(baseRange, board, keepFrac, board);
    return {
      range: combos,
      pct: Math.max(0.5, pct * keepFrac),
      basis: `${basis}, board-weighted to top ${(keepFrac * 100).toFixed(0)}%`
    };
  }

  return { range: baseRange, pct, basis };
}

/**
 * @typedef {object} HandSnapshot
 * @property {string[]} heroCards
 * @property {string[]} board
 * @property {"preflop"|"flop"|"turn"|"river"} street
 * @property {number} pot total pot including live bets
 * @property {number} toCall
 * @property {number} heroBet
 * @property {number} heroStack
 * @property {string} heroPosition
 * @property {number} tableSize players dealt in
 * @property {object[]} villains live (unfolded, non-hero) players:
 *   {name, position, stack, bet, preflopRole, postflopAggressions,
 *    postflopCalls, profile}
 * @property {"unopened"|"limped"|"open"|"3bet"|"4bet+"} [preflopFacing]
 * @property {number} [openSizeBB]
 * @property {number} [bigBlind]
 * @property {number} [iterations]
 * @property {() => number} [rng]
 */

/**
 * Main entry: full recommendation for the current decision point.
 * @param {HandSnapshot} snap
 */
export function advise(snap) {
  const villains = (snap.villains || []).map(v => {
    const est = estimateVillainRange(v, snap.board || [], snap.pot || 0);
    return { ...v, range: est.range, rangePct: est.pct, rangeBasis: est.basis };
  });

  if (snap.street === "preflop") {
    const aggressor = villains.find(v => v.preflopRole === "raiser" || v.preflopRole === "3bettor");
    const pre = preflopAdvice({
      heroCards: snap.heroCards,
      position: snap.heroPosition,
      facing: snap.preflopFacing || (snap.toCall > 0 ? "open" : "unopened"),
      numLimpers: villains.filter(v => v.preflopRole === "limper").length,
      openSizeBB: snap.openSizeBB,
      aggressorProfile: aggressor?.profile,
      tableSize: snap.tableSize,
      style: snap.style
    });
    let recommended = { action: pre.action, size: pre.sizing, ev: null, note: pre.reason };

    // Pot-odds floor: charts are price-blind, so a min-click 3-bet used to
    // fold out hands getting 5:1 closing the action. Any hand with real
    // playability continues when the required equity is tiny.
    if (/FOLD/.test(pre.action) && snap.toCall > 0 && snap.pot > 0) {
      const reqEq = snap.toCall / (snap.pot + snap.toCall);
      const name = handName(snap.heroCards[0], snap.heroCards[1]);
      if (reqEq < 0.22 && chenScore(name) >= 4) {
        recommended = {
          action: "CALL", size: "—", ev: null,
          note: `Pot-odds override: only ${(reqEq * 100).toFixed(0)}% equity needed — ${name} continues despite the chart fold.`
        };
      }
    }

    return {
      street: "preflop",
      kind: "chart",
      recommended,
      actions: [],
      meta: {
        chart: pre.chart,
        confidence: pre.confidence,
        mix: pre.mix,
        exploits: aggressor ? exploitPlan(aggressor.profile).notes : [],
        villains: villainSummaries(villains)
      }
    };
  }

  const post = postflopAdvice({
    heroCards: snap.heroCards,
    board: snap.board,
    pot: snap.pot,
    toCall: snap.toCall,
    heroBet: snap.heroBet,
    heroStack: snap.heroStack,
    villains,
    inPosition: snap.inPosition,
    style: snap.style,
    iterations: snap.iterations,
    rng: snap.rng
  });

  return {
    street: snap.street,
    kind: "ev",
    recommended: post.recommended,
    actions: post.actions,
    meta: { ...post.meta, villains: villainSummaries(villains) }
  };
}

function villainSummaries(villains) {
  return villains.map(v => ({
    name: v.name,
    position: v.position,
    rangePct: v.rangePct,
    rangeBasis: v.rangeBasis,
    profileLabel: v.profile?.label || "UNKNOWN",
    hints: v.profile?.hints || []
  }));
}
