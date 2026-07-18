// Preflop range machinery: range-string parsing, hand ordering, and
// 6-max GTO-style charts (100bb cash baseline).
//
// Range notation supported (PioSOLVER/GTO Wizard style):
//   "AA"          exact pair        "77+"        pairs 77 and up
//   "77-33"       pair span         "ATs"        exact suited
//   "ATs+"        kicker up to K    "A2s-A5s"    suited span (low card varies)
//   "AJo+"        offsuit kicker    "AK"         both AKs and AKo
// Comma-separated lists combine.

import { RANKS, RANK_VALUE, VALUE_RANK, handName, expandCombos } from "./cards.js";

/** Parse one token into a list of 169-grid hand names. */
function parseToken(token) {
  const t = token.trim();
  if (!t) return [];

  // Pair span: "77-33"
  let m = t.match(/^([2-9TJQKA])\1-([2-9TJQKA])\2$/);
  if (m) {
    const hi = RANK_VALUE[m[1]], lo = RANK_VALUE[m[2]];
    const out = [];
    for (let v = Math.min(hi, lo); v <= Math.max(hi, lo); v++) out.push(VALUE_RANK[v] + VALUE_RANK[v]);
    return out;
  }

  // Pair with plus: "77+"
  m = t.match(/^([2-9TJQKA])\1\+$/);
  if (m) {
    const out = [];
    for (let v = RANK_VALUE[m[1]]; v <= 14; v++) out.push(VALUE_RANK[v] + VALUE_RANK[v]);
    return out;
  }

  // Exact pair: "77"
  m = t.match(/^([2-9TJQKA])\1$/);
  if (m) return [t];

  // Suited/offsuit span: "A2s-A5s"
  m = t.match(/^([2-9TJQKA])([2-9TJQKA])([so])-([2-9TJQKA])([2-9TJQKA])([so])$/);
  if (m && m[1] === m[4] && m[3] === m[6]) {
    const hi = m[1], suf = m[3];
    const from = RANK_VALUE[m[2]], to = RANK_VALUE[m[5]];
    const out = [];
    for (let v = Math.min(from, to); v <= Math.max(from, to); v++) out.push(hi + VALUE_RANK[v] + suf);
    return out;
  }

  // Kicker plus: "ATs+" / "AJo+" — low card climbs to one below the high card
  m = t.match(/^([2-9TJQKA])([2-9TJQKA])([so])\+$/);
  if (m) {
    const hiV = RANK_VALUE[m[1]], loV = RANK_VALUE[m[2]], suf = m[3];
    const out = [];
    for (let v = loV; v < hiV; v++) out.push(m[1] + VALUE_RANK[v] + suf);
    return out;
  }

  // Exact non-pair: "ATs" / "AJo"
  m = t.match(/^([2-9TJQKA])([2-9TJQKA])([so])$/);
  if (m) return [t];

  // Both suits: "AK"
  m = t.match(/^([2-9TJQKA])([2-9TJQKA])$/);
  if (m) return [t + "s", t + "o"];

  throw new Error(`Unparseable range token: "${token}"`);
}

/** @param {string} rangeStr @returns {Set<string>} set of 169-grid names */
export function parseRange(rangeStr) {
  const names = new Set();
  for (const token of rangeStr.split(",")) {
    for (const name of parseToken(token)) names.add(name);
  }
  return names;
}

/** All 169 grid names. */
export function allHandNames() {
  const names = [];
  for (let i = RANKS.length - 1; i >= 0; i--) {
    for (let j = RANKS.length - 1; j >= 0; j--) {
      if (i === j) names.push(RANKS[i] + RANKS[j]);
      else if (i > j) names.push(RANKS[i] + RANKS[j] + "s");
      else names.push(RANKS[j] + RANKS[i] + "o");
    }
  }
  return [...new Set(names)];
}

// ─── Hand strength ordering (Chen-formula based) ─────────────────
// Used for percent-based range construction and exploit widening.
// The explicit charts below carry the GTO shape; this ordering is the
// fallback metric, documented as an approximation.

/** Chen formula score for a 169-grid name. */
export function chenScore(name) {
  const v1 = RANK_VALUE[name[0]], v2 = RANK_VALUE[name[1]];
  const hi = Math.max(v1, v2), lo = Math.min(v1, v2);
  const highPts = hi === 14 ? 10 : hi === 13 ? 8 : hi === 12 ? 7 : hi === 11 ? 6 : hi / 2;
  if (v1 === v2) return Math.max(5, highPts * 2);
  let score = highPts;
  if (name[2] === "s") score += 2;
  const gap = hi - lo - 1;
  if (gap === 1) score -= 1;
  else if (gap === 2) score -= 2;
  else if (gap === 3) score -= 4;
  else if (gap >= 4) score -= 5;
  if (gap <= 1 && hi < 12) score += 1; // connector bonus below Q-high
  return score;
}

let cachedOrder = null;

/** All 169 names sorted strongest → weakest. */
export function handOrder() {
  if (!cachedOrder) {
    cachedOrder = allHandNames().sort((a, b) => chenScore(b) - chenScore(a));
  }
  return cachedOrder;
}

/**
 * Top `pct` percent of hands by combo count (1326 total combos).
 * @param {number} pct 0-100 @returns {Set<string>}
 */
export function topPercentRange(pct) {
  const target = (pct / 100) * 1326;
  const names = new Set();
  let combos = 0;
  for (const name of handOrder()) {
    if (combos >= target) break;
    names.add(name);
    combos += expandCombos(name).length;
  }
  return names;
}

/** Percent of all combos contained in a range set. */
export function rangePercent(names) {
  let combos = 0;
  for (const name of names) combos += expandCombos(name).length;
  return (combos / 1326) * 100;
}

/** Expand a range set into concrete combos, excluding blocked cards. */
export function comboize(names, blocked = []) {
  const blockedSet = new Set(blocked);
  const combos = [];
  for (const name of names) {
    for (const combo of expandCombos(name)) {
      if (!blockedSet.has(combo[0]) && !blockedSet.has(combo[1])) combos.push(combo);
    }
  }
  return combos;
}

/** Is a concrete 2-card hand inside a range set? */
export function inRange(names, c1, c2) {
  return names.has(handName(c1, c2));
}

// ─── 6-max GTO-style charts (100bb cash, simplified) ─────────────
// Shapes follow published solver outputs; sizes are the standard
// study-tool baselines. Documented approximation, not a full solve.

export const CHARTS = {
  rfi: {
    UTG: "22+, ATs+, A5s-A4s, KTs+, QTs+, JTs, T9s, 98s, 87s, 76s, AQo+, KQo",
    HJ: "22+, A9s+, A5s-A2s, KTs+, QTs+, J9s+, T9s, 98s, 87s, 76s, 65s, AJo+, KQo",
    CO: "22+, A2s+, K9s+, Q9s+, J9s+, T8s+, 97s+, 87s, 76s, 65s, 54s, ATo+, KJo+, QJo",
    BTN: "22+, A2s+, K2s+, Q5s+, J7s+, T7s+, 96s+, 86s+, 75s+, 64s+, 54s, 43s, A2o+, K9o+, Q9o+, J9o+, T9o, 98o",
    SB: "22+, A2s+, K2s+, Q4s+, J7s+, T7s+, 96s+, 86s+, 75s+, 64s+, 54s, A2o+, K9o+, Q9o+, J9o+, T9o"
  },
  // Facing an open raise (not in the blinds)
  vsRfi: {
    threebetValue: "QQ+, AKs, AKo",
    threebetBluff: "A5s-A4s, KJs, 76s, 65s",
    call: "JJ-22, AQs-ATs, KTs+, QTs+, JTs, T9s, 98s, 87s, AQo, KQo"
  },
  // Big blind defending vs a single open
  bbDefend: {
    threebetValue: "QQ+, AKs, AKo",
    threebetBluff: "A5s-A2s, K9s, Q9s, J9s, 86s, 75s",
    call: "JJ-22, A2s+, K2s+, Q6s+, J7s+, T7s+, 96s+, 85s+, 74s+, 64s+, 53s+, 43s, ATo-A2o, KTo+, QTo+, JTo, T9o, 98o, 87o, 76o"
  },
  // We opened, villain 3-bet us
  vs3bet: {
    fourbetValue: "KK+, AKs",
    fourbetBluff: "A5s-A4s",
    call: "QQ-99, AQs+, AKo, KQs, JTs, T9s"
  }
};

let cachedCharts = null;

/** Parsed chart sets (memoized). Same shape as CHARTS but with Sets. */
export function parsedCharts() {
  if (!cachedCharts) {
    cachedCharts = {};
    for (const [section, entries] of Object.entries(CHARTS)) {
      cachedCharts[section] = {};
      for (const [key, str] of Object.entries(entries)) {
        cachedCharts[section][key] = parseRange(str);
      }
    }
  }
  return cachedCharts;
}
