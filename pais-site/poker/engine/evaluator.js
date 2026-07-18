// 7-card hand evaluator. Fixes the fatal flaw of the v6 engine: it compared
// category only (a pair of 2s tied a pair of aces). Scores here are fully
// ordered integers — higher score always wins, kickers included.
//
// Score layout: category (0-8) in the high bits, then five 4-bit tiebreak
// ranks (each 2..14) packed below: score = cat<<20 | t1<<16 | t2<<12 | ...

import { RANK_VALUE } from "./cards.js";

export const CATEGORY = Object.freeze({
  HIGH_CARD: 0,
  PAIR: 1,
  TWO_PAIR: 2,
  TRIPS: 3,
  STRAIGHT: 4,
  FLUSH: 5,
  FULL_HOUSE: 6,
  QUADS: 7,
  STRAIGHT_FLUSH: 8
});

export const CATEGORY_NAME = Object.freeze([
  "High Card", "Pair", "Two Pair", "Three of a Kind", "Straight",
  "Flush", "Full House", "Four of a Kind", "Straight Flush"
]);

function pack(cat, tiebreaks) {
  let score = cat << 20;
  for (let i = 0; i < 5; i++) {
    score |= (tiebreaks[i] || 0) << (16 - i * 4);
  }
  return score;
}

/**
 * Highest straight value in a list of distinct rank values, or 0.
 * Handles the wheel (A-5) by treating the ace as 1.
 */
function straightHigh(values) {
  const uniq = [...new Set(values)].sort((a, b) => b - a);
  if (uniq.includes(14)) uniq.push(1);
  let run = 1;
  for (let i = 0; i < uniq.length - 1; i++) {
    if (uniq[i] - uniq[i + 1] === 1) {
      run++;
      if (run >= 5) return uniq[i - 3]; // high card of the 5-run
    } else {
      run = 1;
    }
  }
  return 0;
}

/**
 * Evaluate the best 5-card hand from 5-7 cards.
 * @param {string[]} cards e.g. ["As","Kd","Qh","Jc","Ts","2h","3d"]
 * @returns {{score: number, category: number, name: string}}
 */
export function evaluate(cards) {
  const values = cards.map(c => RANK_VALUE[c[0]]);
  const suits = cards.map(c => c[1]);

  // Rank frequency, sorted by (count desc, value desc)
  const freq = new Map();
  for (const v of values) freq.set(v, (freq.get(v) || 0) + 1);
  const groups = [...freq.entries()].sort((a, b) => b[1] - a[1] || b[0] - a[0]);

  // Flush detection
  const suitCount = {};
  suits.forEach(s => { suitCount[s] = (suitCount[s] || 0) + 1; });
  const flushSuit = Object.keys(suitCount).find(s => suitCount[s] >= 5) || null;

  if (flushSuit) {
    const flushValues = cards.filter(c => c[1] === flushSuit).map(c => RANK_VALUE[c[0]]);
    const sfHigh = straightHigh(flushValues);
    if (sfHigh) {
      return { score: pack(CATEGORY.STRAIGHT_FLUSH, [sfHigh]), category: CATEGORY.STRAIGHT_FLUSH, name: sfHigh === 14 ? "Royal Flush" : "Straight Flush" };
    }
  }

  const [top, second] = groups;

  if (top[1] === 4) {
    const kicker = Math.max(...values.filter(v => v !== top[0]));
    return { score: pack(CATEGORY.QUADS, [top[0], kicker]), category: CATEGORY.QUADS, name: CATEGORY_NAME[CATEGORY.QUADS] };
  }

  if (top[1] === 3 && second && second[1] >= 2) {
    return { score: pack(CATEGORY.FULL_HOUSE, [top[0], second[0]]), category: CATEGORY.FULL_HOUSE, name: CATEGORY_NAME[CATEGORY.FULL_HOUSE] };
  }

  if (flushSuit) {
    const flushTop5 = cards
      .filter(c => c[1] === flushSuit)
      .map(c => RANK_VALUE[c[0]])
      .sort((a, b) => b - a)
      .slice(0, 5);
    return { score: pack(CATEGORY.FLUSH, flushTop5), category: CATEGORY.FLUSH, name: CATEGORY_NAME[CATEGORY.FLUSH] };
  }

  const stHigh = straightHigh(values);
  if (stHigh) {
    return { score: pack(CATEGORY.STRAIGHT, [stHigh]), category: CATEGORY.STRAIGHT, name: CATEGORY_NAME[CATEGORY.STRAIGHT] };
  }

  if (top[1] === 3) {
    const kickers = values.filter(v => v !== top[0]).sort((a, b) => b - a).slice(0, 2);
    return { score: pack(CATEGORY.TRIPS, [top[0], ...kickers]), category: CATEGORY.TRIPS, name: CATEGORY_NAME[CATEGORY.TRIPS] };
  }

  if (top[1] === 2 && second && second[1] === 2) {
    // With 7 cards there can be three pairs — take the two highest.
    const pairValues = groups.filter(g => g[1] === 2).map(g => g[0]).sort((a, b) => b - a);
    const [p1, p2] = pairValues;
    const kicker = Math.max(...values.filter(v => v !== p1 && v !== p2));
    return { score: pack(CATEGORY.TWO_PAIR, [p1, p2, kicker]), category: CATEGORY.TWO_PAIR, name: CATEGORY_NAME[CATEGORY.TWO_PAIR] };
  }

  if (top[1] === 2) {
    const kickers = values.filter(v => v !== top[0]).sort((a, b) => b - a).slice(0, 3);
    return { score: pack(CATEGORY.PAIR, [top[0], ...kickers]), category: CATEGORY.PAIR, name: CATEGORY_NAME[CATEGORY.PAIR] };
  }

  const top5 = [...values].sort((a, b) => b - a).slice(0, 5);
  return { score: pack(CATEGORY.HIGH_CARD, top5), category: CATEGORY.HIGH_CARD, name: CATEGORY_NAME[CATEGORY.HIGH_CARD] };
}
