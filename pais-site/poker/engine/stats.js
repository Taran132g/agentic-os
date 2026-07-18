// Opponent modeling v2: beyond VPIP/PFR — c-bet behavior, fold-to-pressure,
// check-raises, showdown-revealed hands. These are what stop the advisor
// from treating every villain the same. Pure module — persistence is the
// extension layer's job.

import { handName } from "./cards.js";
import { handOrder } from "./ranges.js";

export function createStats() {
  return {
    hands: 0,
    vpip: 0,
    pfr: 0,
    threeBet: 0,
    aggActions: 0,
    passiveActions: 0,
    // v2 counters (ensureV2 backfills old persisted objects)
    cbetOpp: 0,        // was preflop aggressor seeing a flop, first to act or checked to
    cbetMade: 0,
    foldToCbetOpp: 0,  // faced a flop c-bet
    foldToCbet: 0,
    foldTo3betOpp: 0,  // opened, got 3-bet
    foldTo3bet: 0,
    checkRaises: 0,
    sawShowdown: 0,
    showdownHands: []  // [{name, pctile, street}] capped — revealed holdings
  };
}

/** Backfill v2 fields on stats persisted by v7.0. */
export function ensureV2(stats) {
  const fresh = createStats();
  for (const k of Object.keys(fresh)) {
    if (stats[k] === undefined) stats[k] = fresh[k];
  }
  return stats;
}

export function createHandFlags() {
  return { vpip: false, pfr: false, threeBet: false };
}

export function recordHandStart(stats) {
  ensureV2(stats);
  stats.hands += 1;
}

/**
 * Record one observed action.
 * @param {object} stats @param {object} flags per-hand flags
 * @param {"preflop"|"flop"|"turn"|"river"} street
 * @param {"fold"|"check"|"call"|"bet"|"raise"} action
 * @param {boolean} facingRaise was there already a raise before this action
 */
export function recordAction(stats, flags, street, action, facingRaise = false) {
  ensureV2(stats);
  if (street === "preflop") {
    if ((action === "call" || action === "raise" || action === "bet") && !flags.vpip) {
      flags.vpip = true;
      stats.vpip += 1;
    }
    if ((action === "raise" || action === "bet") && !flags.pfr) {
      flags.pfr = true;
      stats.pfr += 1;
    }
    if (action === "raise" && facingRaise && !flags.threeBet) {
      flags.threeBet = true;
      stats.threeBet += 1;
    }
    return;
  }
  if (action === "bet" || action === "raise") stats.aggActions += 1;
  else if (action === "call" || action === "check") stats.passiveActions += 1;
}

// ── v2 event recorders (called by tracker/review when detected) ──

export function recordCbetOpportunity(stats, made) {
  ensureV2(stats);
  stats.cbetOpp += 1;
  if (made) stats.cbetMade += 1;
}

export function recordFacedCbet(stats, folded) {
  ensureV2(stats);
  stats.foldToCbetOpp += 1;
  if (folded) stats.foldToCbet += 1;
}

export function recordFaced3bet(stats, folded) {
  ensureV2(stats);
  stats.foldTo3betOpp += 1;
  if (folded) stats.foldTo3bet += 1;
}

export function recordCheckRaise(stats) {
  ensureV2(stats);
  stats.checkRaises += 1;
}

const SHOWDOWN_CAP = 24;

/**
 * Record a revealed holding. Percentile 0 = strongest possible starting
 * hand, 1 = weakest — lets us measure how wide their showdown range runs.
 */
export function recordShowdown(stats, card1, card2, street = "river") {
  ensureV2(stats);
  const name = handName(card1, card2);
  const order = handOrder();
  const idx = order.indexOf(name);
  const pctile = idx === -1 ? 0.5 : idx / (order.length - 1);
  stats.sawShowdown += 1;
  stats.showdownHands.push({ name, pctile, street });
  if (stats.showdownHands.length > SHOWDOWN_CAP) {
    stats.showdownHands = stats.showdownHands.slice(-SHOWDOWN_CAP);
  }
}

const MIN_SAMPLE = 8;      // hands before VPIP-style reads count
const MIN_EVENT_SAMPLE = 5; // opportunities before event-rate reads count

function rate(num, den, minDen = MIN_EVENT_SAMPLE) {
  return den >= minDen ? (num / den) * 100 : null;
}

/**
 * Derive a playable profile from raw counters.
 */
export function deriveProfile(stats) {
  if (!stats || stats.hands < MIN_SAMPLE) {
    return {
      vpip: null, pfr: null, af: null,
      cbet: null, foldToCbet: null, foldTo3bet: null,
      showdownLooseness: null,
      label: "UNKNOWN", rangeWidthMult: 1, foldEquityMult: 1,
      hints: ["Not enough hands for reads — playing GTO baseline."]
    };
  }
  ensureV2(stats);
  const vpip = (stats.vpip / stats.hands) * 100;
  const pfr = (stats.pfr / stats.hands) * 100;
  const totalPostflop = stats.aggActions + stats.passiveActions;
  const af = totalPostflop >= 6 ? stats.aggActions / Math.max(1, stats.passiveActions) : null;

  const cbet = rate(stats.cbetMade, stats.cbetOpp);
  const foldToCbet = rate(stats.foldToCbet, stats.foldToCbetOpp);
  const foldTo3bet = rate(stats.foldTo3bet, stats.foldTo3betOpp, 3);

  // Average strength-percentile of hands they showed down (higher = wider/weaker).
  const showdownLooseness = stats.showdownHands.length >= 3
    ? stats.showdownHands.reduce((s, h) => s + h.pctile, 0) / stats.showdownHands.length
    : null;

  let label = "REG";
  if (vpip < 18) label = "NIT";
  else if (vpip <= 28 && pfr >= vpip - 9) label = "TAG";
  else if (vpip > 45 && af !== null && af >= 2) label = "MANIAC";
  else if (vpip > 38 && (af === null || af < 1)) label = "STATION";
  else if (vpip > 28) label = "LAG";

  // Range width: VPIP baseline, sharpened by what they actually showed down.
  let rangeWidthMult = Math.min(2.5, Math.max(0.5, vpip / 25));
  if (showdownLooseness !== null) {
    // 0.35 pctile ≈ solid range → shrink; 0.65 ≈ any-two → widen.
    rangeWidthMult *= Math.min(1.6, Math.max(0.6, showdownLooseness / 0.5));
  }

  // Fold equity: measured fold-to-cbet beats the label guess when we have it.
  let foldEquityMult =
    label === "NIT" ? 1.35 :
    label === "TAG" ? 1.1 :
    label === "STATION" ? 0.55 :
    label === "MANIAC" ? 0.75 : 1;
  if (foldToCbet !== null) {
    foldEquityMult = Math.min(1.8, Math.max(0.3, foldToCbet / 50));
  }

  const hints = [];
  if (label === "NIT") hints.push("Overfolds — bluff more, fold to their aggression.");
  if (label === "STATION") hints.push("Calls too much — value bet thinner, never bluff.");
  if (label === "MANIAC") hints.push("Over-aggressive — call down lighter, trap with value.");
  if (label === "LAG") hints.push("Wide + aggressive — 3bet value hands, avoid marginal bluffcatches OOP.");
  if (label === "TAG") hints.push("Solid — stay near GTO baseline.");
  if (vpip - pfr > 15) hints.push("Limp/call heavy — isolate wider for value.");
  if (foldToCbet !== null && foldToCbet > 60) hints.push(`Folds to ${foldToCbet.toFixed(0)}% of c-bets — c-bet any two.`);
  if (foldToCbet !== null && foldToCbet < 35) hints.push(`Only folds to ${foldToCbet.toFixed(0)}% of c-bets — c-bet value only.`);
  if (foldTo3bet !== null && foldTo3bet > 65) hints.push(`Folds to ${foldTo3bet.toFixed(0)}% of 3-bets — attack their opens.`);
  if (cbet !== null && cbet > 75) hints.push(`C-bets ${cbet.toFixed(0)}% — their flop bets mean little; float/raise more.`);
  if (stats.checkRaises >= 3) hints.push("Check-raises repeatedly — respect it, bet thinner behind their checks.");
  if (showdownLooseness !== null && showdownLooseness > 0.6) hints.push("Showdowns reveal junk — widen your value range against them.");
  if (showdownLooseness !== null && showdownLooseness < 0.35) hints.push("Only shows up with the goods — their big bets are real.");

  return {
    vpip, pfr, af, cbet, foldToCbet, foldTo3bet, showdownLooseness,
    label, rangeWidthMult, foldEquityMult, hints
  };
}
