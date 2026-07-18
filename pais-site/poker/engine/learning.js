// Self-learning playstyle. Two pieces:
//
//  1. Style dials — tightness & aggression (0..1). Defaults are TIGHT per
//     the owner's preference. They gate preflop range trims, postflop
//     aggression scaling, and the all-in equity threshold.
//  2. Outcome buckets — every hero decision is recorded as
//     (street × equity band × action family) and settled against the
//     hand's net result in big blinds. Buckets with enough samples feed
//     EV adjustments back into the advisor, and persistent losers/winners
//     nudge the dials. The panel explains every learned adjustment.
//
// Pure module: persistence and hand-boundary wiring live in the UI layer.

export const ACTION_FAMILIES = ["fold", "check-call", "bet-raise", "all-in"];

export function actionFamily(actionLabel) {
  const a = String(actionLabel).toUpperCase();
  if (a.includes("ALL-IN")) return "all-in";
  if (a.includes("FOLD")) return "fold";
  if (a.includes("CHECK") || a.includes("CALL") || a.includes("LIMP")) return "check-call";
  return "bet-raise";
}

export function equityBand(eq01) {
  if (eq01 === null || eq01 === undefined) return "pre";
  if (eq01 < 0.40) return "<40";
  if (eq01 < 0.55) return "40-55";
  if (eq01 < 0.70) return "55-70";
  if (eq01 < 0.85) return "70-85";
  return "85+";
}

export function bucketKey(street, eq01, family) {
  return `${street}|${equityBand(eq01)}|${family}`;
}

/** Fresh learning state. TAG baseline: tight preflop, aggressive postflop exploitation. */
export function createLearningState() {
  return {
    version: 1,
    tightness: 0.58,   // 0 = loosest, 1 = nittiest — TAG baseline (was 0.75)
    aggression: 0.88,  // 0 = passive, 1 = hyper-aggro — TAG baseline (was 0.35)
    handsLearned: 0,
    buckets: {},       // key → {n, sumNetBB, sumNetPot}
    notes: []          // human-readable learned lessons (latest last)
  };
}

/** Backfill/repair a persisted state. */
export function ensureLearningState(state) {
  const fresh = createLearningState();
  if (!state || typeof state !== "object") return fresh;
  for (const k of Object.keys(fresh)) {
    if (state[k] === undefined) state[k] = fresh[k];
  }
  state.tightness = clamp01(state.tightness);
  state.aggression = clamp01(state.aggression);
  return state;
}

function clamp01(x) { return Math.min(1, Math.max(0, Number(x) || 0)); }

/**
 * Record one hero decision during a hand (kept in a pending list by the
 * caller until the hand settles).
 * @returns {{key: string, street: string, eq01: number|null, family: string, potBB: number}}
 */
export function pendingDecision({ street, equity, actionLabel, pot, bigBlind }) {
  const eq01 = equity === null || equity === undefined ? null : equity / 100;
  const family = actionFamily(actionLabel);
  return {
    key: bucketKey(street, eq01, family),
    street,
    eq01,
    family,
    potBB: bigBlind > 0 ? pot / bigBlind : pot
  };
}

const MIN_BUCKET_SAMPLE = 3;
const NOTE_CAP = 12;

/**
 * Settle a finished hand into the learning state.
 * @param {object} state learning state (mutated + returned)
 * @param {object[]} decisions pendingDecision() outputs from this hand
 * @param {number} netBB hero chips won/lost this hand, in big blinds
 */
export function finishHand(state, decisions, netBB) {
  ensureLearningState(state);
  if (!decisions?.length || !Number.isFinite(netBB)) return state;
  state.handsLearned += 1;

  for (const d of decisions) {
    if (!state.buckets[d.key]) state.buckets[d.key] = { n: 0, sumNetBB: 0, sumNetPot: 0 };
    const b = state.buckets[d.key];
    b.n += 1;
    b.sumNetBB += netBB;
    b.sumNetPot += d.potBB > 0 ? netBB / d.potBB : 0;

    const aggressive = d.family === "bet-raise" || d.family === "all-in";
    const marginal   = d.eq01 !== null && d.eq01 < 0.85;

    // Aggressive line with sub-85% equity that loses → pull back slightly
    if (aggressive && marginal) {
      if (netBB < 0) {
        state.aggression = clamp01(state.aggression - 0.012);
        state.tightness  = clamp01(state.tightness  + 0.008);
      } else if (netBB > 0) {
        state.aggression = clamp01(state.aggression + 0.015);
      }
    }
    // Called with <50% equity and lost → fold this spot going forward
    if (d.family === "check-call" && d.eq01 !== null && d.eq01 < 0.50 && netBB < 0) {
      state.tightness = clamp01(state.tightness + 0.008);
    }
    // Large all-in loss → tighten jam threshold
    if (d.family === "all-in" && netBB < -8) {
      state.tightness  = clamp01(state.tightness  + 0.012);
      state.aggression = clamp01(state.aggression - 0.012);
    }
    // Loose preflop play that loses → tighten preflop range
    if (d.street === "preflop" && d.family !== "fold" && netBB < -3) {
      state.tightness = clamp01(state.tightness + 0.008);
    }
  }

  // Never let tightness creep above 0.82 — preserve exploitative aggression
  state.tightness = Math.min(0.82, state.tightness);

  refreshNotes(state);
  return state;
}

function refreshNotes(state) {
  const notes = [];
  for (const [key, b] of Object.entries(state.buckets)) {
    if (b.n < MIN_BUCKET_SAMPLE + 1) continue;
    const avg = b.sumNetBB / b.n;
    const [street, band, family] = key.split("|");
    if (family === "all-in" && avg < -1) {
      notes.push(`All-in on the ${street} with ${band}% equity: ${avg.toFixed(1)}bb avg over ${b.n} tries — jam threshold raised.`);
    } else if (family === "bet-raise" && avg < -2) {
      notes.push(`Betting/raising the ${street} at ${band}% equity is losing ${(-avg).toFixed(1)}bb/hand (${b.n} samples) — downgraded.`);
    } else if (family === "check-call" && avg < -3) {
      notes.push(`Calling down on the ${street} at ${band}% equity is bleeding ${(-avg).toFixed(1)}bb/hand (${b.n} samples) — folding more.`);
    } else if ((family === "bet-raise" || family === "all-in") && avg > 3) {
      notes.push(`Aggression on the ${street} at ${band}% equity is printing +${avg.toFixed(1)}bb/hand (${b.n} samples) — kept.`);
    }
  }
  state.notes = notes.slice(-NOTE_CAP);
}

/**
 * Convert the learning state into concrete advisor adjustments.
 * Handles null/undefined state gracefully by returning fresh defaults.
 */
export function styleAdjustments(state) {
  const s = ensureLearningState(state);
  return {
    tightness: s.tightness,
    aggression: s.aggression,
    // Floor at 0.82: even the loosest agent needs 82%+ raw equity to jam.
    // Tight 0.75 → 0.91; Loose 0.30 → 0.86. Marginal made-hands add 0.05 on top.
    allInEqThreshold: 0.82 + 0.12 * s.tightness,
    // Demand a cushion above raw pot odds when calling; never go negative.
    callEquityMarginPct: Math.max(0, 6 * (s.tightness - 0.40)),
    // Fraction of each preflop chart kept (top of chart survives).
    preflopKeepFraction: Math.min(1, 1.15 - 0.5 * s.tightness),
    // EV scaling for aggressive lines: passive style shades them down.
    aggressionEvShift: (s.aggression - 0.5) * 0.25,
    /**
     * Learned per-bucket EV delta (chips) for a candidate action.
     * Positive = this line has been winning for the hero, negative = losing.
     */
    bucketEvDelta(street, eq01, actionLabel, pot) {
      const b = s.buckets[bucketKey(street, eq01, actionFamily(actionLabel))];
      if (!b || b.n < MIN_BUCKET_SAMPLE) return 0;
      const avgPerPot = b.sumNetPot / b.n;
      return Math.max(-0.4, Math.min(0.4, avgPerPot * 0.3)) * pot;
    }
  };
}

/**
 * Train from a reviewed session (parsed PokerNow CSV). Uses each hero
 * decision the review produced and the hand's replayed net result — the
 * "self-learn from previous hands" path.
 * @param {object} state
 * @param {object[]} results reviewSession().results (with heroNet)
 * @param {number} bigBlind
 * @returns {number} hands trained on
 */
export function trainFromReview(state, results, bigBlind) {
  ensureLearningState(state);
  let trained = 0;
  for (const r of results) {
    if (!Number.isFinite(r.heroNet)) continue;
    const netBB = bigBlind > 0 ? r.heroNet / bigBlind : r.heroNet;
    const decisions = r.decisions.map(d => pendingDecision({
      street: d.street,
      equity: d.meta?.equity ?? null,
      actionLabel: d.actual,           // learn from what the HERO actually did
      pot: d.pot,
      bigBlind
    }));
    finishHand(state, decisions, netBB);
    trained += 1;
  }
  return trained;
}
