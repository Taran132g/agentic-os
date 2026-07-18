// Postflop EV advisor. For every legal action it estimates chip EV relative
// to folding now, using range-vs-range Monte Carlo equity, pot odds, MDF and
// profile-adjusted fold equity. These are documented heuristics, not a CFR
// solve — labels in the output say so when confidence drops (multiway, thin
// fold-equity estimates).

import { equityVsRanges } from "./equity.js";
import { RANK_VALUE } from "./cards.js";
import { exploitPlan, mergePlans } from "./exploit.js";
import { evaluate, CATEGORY } from "./evaluator.js";

/** Board texture summary (kept from v6, extended with danger score). */
export function boardTexture(board) {
  if (board.length < 3) return null;
  const values = board.map(c => RANK_VALUE[c[0]]);
  const suits = board.map(c => c[1]);
  const suitCount = {};
  suits.forEach(s => { suitCount[s] = (suitCount[s] || 0) + 1; });
  const maxSuit = Math.max(...Object.values(suitCount));
  const sorted = [...values].sort((a, b) => a - b);
  let connected = 0;
  for (let i = 0; i < sorted.length - 1; i++) {
    if (sorted[i + 1] - sorted[i] <= 2) connected++;
  }
  const high = values.filter(v => v >= 11).length;
  const paired = new Set(values).size < values.length;
  return {
    suits: maxSuit >= 3 ? "MONOTONE" : maxSuit === 2 ? "TWO-TONE" : "RAINBOW",
    connectivity: connected >= 2 ? "WET" : connected >= 1 ? "SEMI-WET" : "DRY",
    height: high >= 2 ? "HIGH" : high >= 1 ? "MID" : "LOW",
    paired,
    danger: (maxSuit >= 2 ? 1 : 0) + (connected >= 2 ? 1 : 0) + (paired ? 1 : 0)
  };
}

/** Minimum defense frequency vs a bet of `bet` into `pot`. */
export function mdf(pot, bet) {
  return pot + bet <= 0 ? 1 : pot / (pot + bet);
}

/** Pot odds as required equity percent when calling `toCall` into `pot`. */
export function potOddsPct(pot, toCall) {
  return toCall <= 0 ? 0 : (toCall / (pot + toCall)) * 100;
}

/**
 * Estimated chance ALL villains fold to a bet/raise of `size` into `pot`.
 * GTO baseline: a size-s bet should generate ~s/(pot+s) folds; profiles
 * scale it; multiway compounds it.
 */
function foldEquity(pot, size, villains) {
  const base = size / (pot + size); // 1 - MDF
  let fe = 1;
  for (const v of villains) {
    const profileMult = v.profile ? v.profile.foldEquityMult : 1;
    // A villain who has already raised holds a narrow, strong range and won't fold
    // much to a re-raise. Each postflop aggression reduces their folding probability.
    const aggPenalty = Math.pow(0.72, v.postflopAggressions || 0);
    // A villain who has already called this hand has shown they connect —
    // each call makes the next barrel less likely to fold them out.
    const callPenalty = Math.pow(0.85, v.postflopCalls || 0);
    fe *= Math.min(0.9, Math.max(0.03, base * profileMult * aggPenalty * callPenalty));
  }
  return Math.min(0.85, Math.max(0.02, fe));
}

/**
 * @typedef {object} PostflopSituation
 * @property {string[]} heroCards
 * @property {string[]} board 3-5 cards
 * @property {number} pot total pot INCLUDING all bets currently on the table
 * @property {number} toCall amount hero must add to continue
 * @property {number} heroBet hero's chips already in this street
 * @property {number} heroStack chips behind
 * @property {{name: string, range: Set<string>, profile?: object, stack: number}[]} villains live opponents
 * @property {boolean} [inPosition] hero acts last among live players
 * @property {number} [iterations]
 * @property {() => number} [rng]
 */

/**
 * Rank every available action by estimated EV.
 * @param {PostflopSituation} sit
 */
export function postflopAdvice(sit) {
  const { pot, toCall, heroStack, villains } = sit;
  const iterations = sit.iterations || 1800;
  const eqResult = equityVsRanges(
    sit.heroCards, sit.board, villains.map(v => v.range),
    { iterations, rng: sit.rng }
  );
  const eq = eqResult.equity / 100;
  const texture = boardTexture(sit.board);
  const multiway = villains.length > 1;
  const spr = pot > 0 ? heroStack / pot : 99;
  const villainBet = Math.max(0, ...villains.map(v => v.bet || 0));
  const street = sit.board.length === 3 ? "flop" : sit.board.length === 4 ? "turn" : "river";
  const style = sit.style || null;
  const styleNotes = [];

  // Made-hand category drives stack-off discipline: raw equity for one pair
  // heads-up looks great vs a WHOLE range, but the part of the range that
  // CALLS a jam crushes one pair. Category-aware haircut fixes that.
  const madeCat = evaluate([...sit.heroCards, ...sit.board]).category;
  const weakMade = madeCat <= CATEGORY.TWO_PAIR;
  let calledEqFactor =
    madeCat <= CATEGORY.PAIR ? 0.68 :
    madeCat === CATEGORY.TWO_PAIR ? 0.76 : 0.88;
  if (multiway) calledEqFactor *= 0.88;

  // Jam discipline: style sets the equity bar; weak made hands raise it more.
  const jamThreshold = (style ? style.allInEqThreshold : 0.80) + (weakMade ? 0.05 : 0);
  const jamOk = eq >= jamThreshold;
  if (!jamOk && weakMade && eq > 0.55) {
    styleNotes.push(`ALL-IN suppressed: ${madeCat === CATEGORY.PAIR ? "one pair" : "two pair"} at ${(eq * 100).toFixed(0)}% equity is below the ${(jamThreshold * 100).toFixed(0)}% stack-off bar — calling ranges crush weak made hands.`);
  }

  const actions = [];

  if (toCall > 0) {
    // ── Facing a bet ──
    const callAll = Math.min(toCall, heroStack);
    actions.push({
      action: "FOLD", size: 0, ev: 0,
      note: "Baseline — all EVs are relative to folding here."
    });
    actions.push({
      action: "CALL", size: callAll,
      ev: eq * (pot + callAll) - callAll,
      note: `Need ${potOddsPct(pot, callAll).toFixed(1)}% equity, have ${eqResult.equity.toFixed(1)}%.`
    });

    // Raise to ~3x the bet, and jam when short — but only stack off weak
    // made hands past the jam threshold.
    let raiseTo = Math.min(heroStack + sit.heroBet, Math.round(villainBet * 3 + (pot - villainBet) * 0.5));
    let risk = raiseTo - sit.heroBet;
    if (risk >= heroStack && !jamOk) {
      // Would be all-in: cap to a non-committal raise instead, if possible.
      raiseTo = Math.round(villainBet * 2.2 + sit.heroBet);
      risk = raiseTo - sit.heroBet;
    }
    if (raiseTo > toCall + sit.heroBet && heroStack > toCall && (risk < heroStack || jamOk)) {
      const fe = foldEquity(pot, risk - toCall, villains);
      const eqCalled = eq * calledEqFactor;
      actions.push({
        action: risk >= heroStack ? "ALL-IN" : "RAISE",
        size: raiseTo,
        ev: fe * pot + (1 - fe) * (eqCalled * (pot + 2 * risk - villainBet) - risk),
        note: `~${(fe * 100).toFixed(0)}% estimated fold equity${multiway ? " (multiway-compounded)" : ""}.`
      });
    }
  } else {
    // ── Checked to hero / hero first to act ──
    actions.push({
      action: "CHECK", size: 0,
      ev: eq * pot,
      note: "Realize equity for free."
    });
    const sizes = [
      { label: "BET 33%", frac: 0.33 },
      { label: "BET 66%", frac: 0.66 },
      { label: "BET 125%", frac: 1.25 }
    ];
    for (const s of sizes) {
      const bet = Math.min(heroStack, Math.max(1, Math.round(pot * s.frac)));
      if (bet <= 0) continue;
      if (bet >= heroStack && !jamOk) continue; // sizing would stack us off
      const fe = foldEquity(pot, bet, villains);
      const eqCalled = eq * calledEqFactor;
      actions.push({
        action: bet >= heroStack ? "ALL-IN" : s.label,
        size: bet,
        ev: fe * pot + (1 - fe) * (eqCalled * (pot + 2 * bet) - bet),
        note: `~${(fe * 100).toFixed(0)}% estimated fold equity.`
      });
    }
    if (spr < 1.5 && jamOk) {
      const bet = heroStack;
      const fe = foldEquity(pot, bet, villains);
      actions.push({
        action: "ALL-IN", size: bet,
        ev: fe * pot + (1 - fe) * (eq * calledEqFactor * (pot + 2 * bet) - bet),
        note: `SPR ${spr.toFixed(1)} — stack goes in easily.`
      });
    }

    // Value-bet nudge: the naive CHECK EV (eq × pot) assumes hero realizes
    // full equity for free, which overrates checking strong made hands —
    // in practice checking gives villains free cards and loses value bets.
    if (eq >= 0.62 && madeCat >= CATEGORY.PAIR) {
      const checkAction = actions.find(a => a.action === "CHECK");
      if (checkAction) {
        checkAction.ev -= (eq - 0.62) * 1.2 * pot;
        checkAction.note = "Checking a strong hand forfeits value bets. " + (checkAction.note || "");
      }
    }
  }

  // ── Exploit layer: reshape EVs per the merged opponent plan ──
  const plan = sit.exploit || mergePlans(villains.map(v => exploitPlan(v.profile)));
  applyExploits(actions, plan, { eq, pot, toCall, multiway });

  // ── Style + self-learning layer ──
  if (style) {
    for (const a of actions) {
      const aggressive = /BET|RAISE|ALL-IN/.test(a.action);
      // Passive/tight style shades marginal aggression down (and vice versa).
      if (aggressive) a.ev += style.aggressionEvShift * pot;
      // Tight call margin: demand a cushion over raw pot odds.
      if (a.action === "CALL" && toCall > 0) {
        a.ev -= (style.callEquityMarginPct / 100) * (pot + toCall);
      }
      // Learned buckets: lines that have lost the hero money get shaded.
      const delta = style.bucketEvDelta(street, eq, a.action, pot);
      if (delta !== 0) {
        a.ev += delta;
        a.note = `${delta > 0 ? "▲" : "▼"} learned: this line has ${delta > 0 ? "made" : "lost"} you money in similar spots. ` + (a.note || "");
      }
    }
  }

  // ── Weak-hand continuation floor ──────────────────────────────────────────
  // When villain has shown postflop aggression (bet or raised), their range is
  // strong. Hero must have top pair or better to continue profitably.
  // Sub-top-pair (middle pair, bottom pair, overcards, board-pair-only) with
  // low equity gets a large EV penalty on all non-fold actions, making fold
  // the dominant choice and preventing the "KQ 5-bet on T-T-2" / "KJ 3-barrel"
  // mistakes.
  const totalVillainAgg = villains.reduce((s, v) => s + (v.postflopAggressions || 0), 0);
  if (totalVillainAgg >= 1 && madeCat <= CATEGORY.PAIR) {
    const boardRanks = new Set(sit.board.map(c => c[0]));
    const heroPersonalPair = sit.heroCards.some(c => boardRanks.has(c[0]));
    const heroIsPocket = sit.heroCards[0][0] === sit.heroCards[1][0];
    const topBoardRank = Math.max(...sit.board.map(c => RANK_VALUE[c[0]]));
    // "Top pair or better" = has personal pair at the top rank, or overpair.
    const heroHasTopPairPlus =
      (heroIsPocket && sit.heroCards.every(c => RANK_VALUE[c[0]] > topBoardRank)) ||
      (!heroIsPocket && sit.heroCards.some(
        c => boardRanks.has(c[0]) && RANK_VALUE[c[0]] === topBoardRank
      ));

    if (!heroHasTopPairPlus) {
      // Equity thresholds: middle/bottom pair needs more equity than pure air
      // to justify continuing (strong draws pass through via higher equity).
      const threshold = heroPersonalPair ? 0.38 : 0.32;
      if (eq < threshold) {
        const penalty = 2.0 * pot;
        for (const a of actions) {
          if (a.action !== "FOLD") a.ev -= penalty;
        }
        styleNotes.push(
          `WEAK vs aggression: ${heroPersonalPair ? "sub-top pair" : "no board pair"} ` +
          `(${(eq * 100).toFixed(0)}% eq) — fold strongly favored over ${totalVillainAgg} aggressive action(s).`
        );
      }
    }
  }

  // ── Equity safety rails ──────────────────────────────────────────────────────
  if (toCall > 0) {
    const reqFrac = toCall / (pot + toCall);
    // Floor: any MC equity below pot odds could be range-estimation noise — fold.
    if (eq < reqFrac) {
      for (const a of actions) {
        if (a.action !== "FOLD") a.ev = Math.min(a.ev, -(pot + toCall + 1));
      }
    }
    // Ceiling: overwhelming equity must never fold — CALL floor prevents it.
    if (eq >= Math.max(0.45, reqFrac * 2)) {
      const callAct = actions.find(a => a.action === "CALL");
      if (callAct) callAct.ev = Math.max(callAct.ev, 0.01 * pot);
    }
  }

  // ── Board-texture equity guards ───────────────────────────────────────────
  // Range estimation is optimistic when villain holds the concentrated-value
  // fraction of their range (e.g., flush on a monotone board, boat on a paired
  // board). Apply extra equity margin on these textures so close calls fold.
  if (toCall > 0 && totalVillainAgg >= 1) {
    const rfBT = toCall / (pot + toCall);
    const bRanks = sit.board.map(c => c[0]);
    const boardIsPaired = new Set(bRanks).size < bRanks.length;
    const sc = {};
    for (const c of sit.board) { sc[c[1]] = (sc[c[1]] || 0) + 1; }
    const maxSuitOnBoard = Math.max(0, ...Object.values(sc));
    const boardIsMonotone = maxSuitOnBoard >= 3;
    const heroHasFlushSuit = boardIsMonotone && sit.heroCards.some(c => (sc[c[1]] || 0) >= 3);

    let extra = 0;
    // Paired board: bets signal boats/trips; TWO_PAIR or weaker is vulnerable.
    // Raise to 0.18 — on boards with a paired rank, villain 7-x / K-x / etc.
    // makes trips at >30% of the contracted range, routinely exceeding 0.12 floor.
    if (boardIsPaired && madeCat <= CATEGORY.TWO_PAIR) extra = Math.max(extra, 0.18);
    // Monotone board: bets signal flush; non-flush hands are vulnerable.
    if (boardIsMonotone && !heroHasFlushSuit && madeCat < CATEGORY.FLUSH) extra = Math.max(extra, 0.14);
    // Villain aggression with TRIPS or weaker: require modest extra cushion.
    if (madeCat <= CATEGORY.TRIPS) extra = Math.max(extra, 0.06);
    // One pair faces concentrated range density (sets, two pair, better pairs)
    // at far higher rates than MC range estimation captures — require more cushion.
    if (madeCat <= CATEGORY.PAIR) extra = Math.max(extra, 0.15);
    // Connected board: even a made straight can be dominated by a higher straight.
    // Range estimation underweights the top end of villain's straight combos.
    const bvBT = sit.board.map(c => RANK_VALUE[c[0]]).sort((a, b) => a - b);
    const boardIsConnected = bvBT[bvBT.length - 1] - bvBT[0] <= 4;
    if (boardIsConnected && madeCat <= CATEGORY.STRAIGHT) extra = Math.max(extra, 0.08);

    // Multiway: each extra active villain beyond heads-up raises the probability
    // that someone holds the dominant hand. Scale any existing guard margin up.
    if (extra > 0 && (sit.tableSize || 2) > 2) {
      extra += ((sit.tableSize || 2) - 2) * 0.015;
    }

    if (extra > 0 && eq < rfBT + extra) {
      for (const a of actions) {
        if (a.action !== "FOLD") a.ev = Math.min(a.ev, -(pot + toCall + 1));
      }
    }
  }

  // ── Turn/river pair: no bluff raises ──────────────────────────────────────
  // No semi-bluff value on turn or river. Raising with one pair against an
  // aggressive villain means their calling range crushes us at showdown.
  if ((street === "turn" || street === "river") && madeCat <= CATEGORY.PAIR && totalVillainAgg >= 1 && toCall > 0) {
    for (const a of actions) {
      if (a.action === "RAISE") a.ev = Math.min(a.ev, -(pot + toCall + 1));
    }
  }

  // ── Turn/river underpair: no bet into passive villains ──────────────────
  // Barreling with a pair lower than the board's highest card risks running
  // into a slowplayed set or two-pair. Hero's best card must at least match
  // the board max to justify putting money in on late streets.
  if ((street === "turn" || street === "river") && madeCat <= CATEGORY.PAIR && toCall === 0) {
    const heroMaxVal = Math.max(...sit.heroCards.map(c => RANK_VALUE[c[0]]));
    const boardMaxVal = Math.max(...sit.board.map(c => RANK_VALUE[c[0]]));
    if (boardMaxVal > heroMaxVal) {
      for (const a of actions) {
        if (a.action === "RAISE") a.ev = Math.min(a.ev, -(pot + 1));
      }
    }
  }

  // De-dupe by action label keeping best EV, then rank.
  const best = new Map();
  for (const a of actions) {
    if (!best.has(a.action) || best.get(a.action).ev < a.ev) best.set(a.action, a);
  }
  const ranked = [...best.values()].sort((a, b) => b.ev - a.ev);

  return {
    actions: ranked,
    recommended: ranked[0],
    meta: {
      equity: eqResult.equity,
      win: eqResult.win,
      tie: eqResult.tie,
      potOdds: toCall > 0 ? potOddsPct(pot, Math.min(toCall, heroStack)) : null,
      mdf: villainBet > 0 ? mdf(pot - villainBet, villainBet) * 100 : null,
      spr,
      texture,
      multiway,
      exploits: plan.notes,
      styleNotes,
      handCategory: madeCat,
      caveat: multiway
        ? "Multiway pot — no GTO equilibrium exists for 3+ players; EVs are equity/MDF heuristics."
        : "Heads-up EV heuristic (equity + fold-equity model), not a full CFR solve."
    }
  };
}

/**
 * Reshape action EVs according to the exploit plan. Adjustment sizes are
 * fractions of the pot — deliberate, documented heuristics.
 */
function applyExploits(actions, plan, ctx) {
  const { eq, pot, toCall, multiway } = ctx;
  const isBluffSpot = eq < 0.45;
  const isStrong = eq > 0.72;
  const isThinValue = eq >= 0.52 && eq <= 0.68;

  for (const a of actions) {
    const isAggressive = /BET|RAISE|ALL-IN/.test(a.action);

    if (plan.neverBluff && isAggressive && isBluffSpot) {
      a.ev -= 0.5 * pot;
      a.note = "✗ EXPLOIT: they never fold — bluffing is lighting money on fire. " + (a.note || "");
    }
    if (plan.valueThin && isAggressive && isThinValue) {
      a.ev += 0.12 * pot;
      a.note = "★ EXPLOIT: they call too wide — this thin value bet prints. " + (a.note || "");
    }
    if (plan.trapWorthy && isStrong && !multiway) {
      if (a.action === "CHECK") {
        a.ev += 0.22 * pot;
        a.note = "★ TRAP: check — they barrel too much; spring the raise when they bet. " + (a.note || "");
      }
      if (a.action === "CALL" && toCall > 0) {
        a.ev += 0.15 * pot;
        a.note = "★ TRAP: flat-call — keep their bluffs alive, raise a later street. " + (a.note || "");
      }
    }
    if (plan.respectRaises && a.action === "CALL" && toCall > 0.5 * pot) {
      a.ev -= 0.08 * pot;
      a.note = "⚠ EXPLOIT: their big bets are underbluffed — lean fold at the margin. " + (a.note || "");
    }
    if (plan.floatCbets && a.action === "CALL" && toCall > 0 && toCall <= 0.5 * pot && !isStrong) {
      a.ev += 0.08 * pot;
      a.note = "★ FLOAT: their c-bet is air-heavy — call and take it away later. " + (a.note || "");
    }
  }
}
