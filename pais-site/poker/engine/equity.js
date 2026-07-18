// Monte Carlo equity vs opponent RANGES (not random hands — the v6 engine
// simulated random villains, which badly overstates equity against players
// whose actions have narrowed their range).

import { buildDeck, shuffled } from "./cards.js";
import { comboize } from "./ranges.js";
import { evaluate } from "./evaluator.js";

/**
 * Hero equity vs 1+ villains, each holding a range.
 *
 * @param {string[]} heroCards two cards
 * @param {string[]} board 0-5 cards
 * @param {(Set<string>|string[][])[]} villainRanges per live villain: either
 *   a set of 169-grid names ("AKs") or an array of concrete combos
 *   (e.g. [["Ah","Kh"], ...]) from board-weighted estimation
 * @param {object} [opts]
 * @param {number} [opts.iterations=2000]
 * @param {() => number} [opts.rng=Math.random]
 * @returns {{equity: number, win: number, tie: number, samples: number}}
 *   equity/win/tie as percentages 0-100
 */
export function equityVsRanges(heroCards, board, villainRanges, opts = {}) {
  const { iterations = 2000, rng = Math.random } = opts;
  const dead = [...heroCards, ...board];

  // Pre-expand each villain's combos minus known dead cards. Combos may
  // still collide with each other's sampled cards — resolved per iteration.
  const deadSet = new Set(dead);
  const villainCombos = villainRanges.map(range => {
    const combos = Array.isArray(range)
      ? range.filter(c => !deadSet.has(c[0]) && !deadSet.has(c[1]))
      : comboize(range, dead);
    // A range so blocked it has no combos degrades to "random hand" (null).
    return combos.length > 0 ? combos : null;
  });

  const baseDeck = buildDeck(dead);
  let win = 0, tie = 0, samples = 0;

  for (let i = 0; i < iterations; i++) {
    const taken = new Set();
    const villainHands = [];
    let failed = false;

    // Sample each villain hand from their range (rejection sampling).
    for (const combos of villainCombos) {
      let hand = null;
      if (combos) {
        for (let attempt = 0; attempt < 30; attempt++) {
          const c = combos[Math.floor(rng() * combos.length)];
          if (!taken.has(c[0]) && !taken.has(c[1])) { hand = c; break; }
        }
      }
      if (!hand) {
        // Range blocked out — deal random from remaining deck.
        const remaining = baseDeck.filter(c => !taken.has(c));
        if (remaining.length < 2) { failed = true; break; }
        const d = shuffled(remaining, rng);
        hand = [d[0], d[1]];
      }
      taken.add(hand[0]);
      taken.add(hand[1]);
      villainHands.push(hand);
    }
    if (failed) continue;

    // Complete the board from what's left.
    const need = 5 - board.length;
    let fullBoard = board;
    if (need > 0) {
      const remaining = baseDeck.filter(c => !taken.has(c));
      const d = shuffled(remaining, rng);
      fullBoard = [...board, ...d.slice(0, need)];
    }

    const heroScore = evaluate([...heroCards, ...fullBoard]).score;
    let bestVillain = -1;
    for (const vh of villainHands) {
      const s = evaluate([...vh, ...fullBoard]).score;
      if (s > bestVillain) bestVillain = s;
    }

    if (heroScore > bestVillain) win++;
    else if (heroScore === bestVillain) tie++;
    samples++;
  }

  if (samples === 0) return { equity: 0, win: 0, tie: 0, samples: 0 };
  return {
    equity: ((win + tie / 2) / samples) * 100,
    win: (win / samples) * 100,
    tie: (tie / samples) * 100,
    samples
  };
}
