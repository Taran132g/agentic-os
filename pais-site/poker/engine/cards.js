// Card primitives shared by every engine module.
// A card is a 2-char string: rank + suit, e.g. "As", "Td", "9c".

export const RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A"];
export const SUITS = ["s", "h", "d", "c"];

/** Rank char → numeric value (2..14). */
export const RANK_VALUE = Object.freeze({
  2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8, 9: 9,
  T: 10, J: 11, Q: 12, K: 13, A: 14
});

export const VALUE_RANK = Object.freeze(
  Object.fromEntries(Object.entries(RANK_VALUE).map(([r, v]) => [v, r]))
);

export const SUIT_SYMBOL = Object.freeze({ s: "♠", h: "♥", d: "♦", c: "♣" });
export const SYMBOL_SUIT = Object.freeze({ "♠": "s", "♥": "h", "♦": "d", "♣": "c" });

/** @param {string} card @returns {boolean} */
export function isValidCard(card) {
  return typeof card === "string" && card.length === 2 &&
    RANK_VALUE[card[0]] !== undefined && SUITS.includes(card[1]);
}

/** Full 52-card deck, minus any cards in `excluded`. */
export function buildDeck(excluded = []) {
  const used = new Set(excluded);
  const deck = [];
  for (const r of RANKS) {
    for (const s of SUITS) {
      const c = r + s;
      if (!used.has(c)) deck.push(c);
    }
  }
  return deck;
}

/** Fisher-Yates shuffle. Returns a new array. */
export function shuffled(deck, rng = Math.random) {
  const d = [...deck];
  for (let i = d.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1));
    [d[i], d[j]] = [d[j], d[i]];
  }
  return d;
}

/**
 * Canonical 169-grid name for a 2-card hand: "AA", "AKs", "AKo".
 * @param {string} c1 @param {string} c2
 */
export function handName(c1, c2) {
  const v1 = RANK_VALUE[c1[0]], v2 = RANK_VALUE[c2[0]];
  const [hi, lo] = v1 >= v2 ? [c1, c2] : [c2, c1];
  if (hi[0] === lo[0]) return hi[0] + lo[0];
  return hi[0] + lo[0] + (hi[1] === lo[1] ? "s" : "o");
}

/**
 * Expand a 169-grid name into concrete combos.
 * "AA" → 6 combos, "AKs" → 4, "AKo" → 12.
 * @param {string} name @returns {string[][]} array of [card, card]
 */
export function expandCombos(name) {
  const r1 = name[0], r2 = name[1];
  const combos = [];
  if (r1 === r2) {
    for (let i = 0; i < SUITS.length; i++) {
      for (let j = i + 1; j < SUITS.length; j++) {
        combos.push([r1 + SUITS[i], r2 + SUITS[j]]);
      }
    }
    return combos;
  }
  const suited = name[2] === "s";
  for (const s1 of SUITS) {
    for (const s2 of SUITS) {
      if (suited && s1 !== s2) continue;
      if (!suited && s1 === s2) continue;
      combos.push([r1 + s1, r2 + s2]);
    }
  }
  return combos;
}

/** Parse "3♥" / "10♠" / "Th" style tokens into internal "3h"/"Ts" form. Returns null if unparseable. */
export function parseCardToken(token) {
  if (!token) return null;
  const t = token.trim();
  // Symbol suit form: "3♥", "10♠"
  const sym = t.match(/^(10|[2-9TJQKA])([♠♥♦♣])$/i);
  if (sym) {
    const rank = sym[1] === "10" ? "T" : sym[1].toUpperCase();
    return rank + SYMBOL_SUIT[sym[2]];
  }
  // Letter suit form: "Th", "As", "9c"
  const letter = t.match(/^(10|[2-9TJQKA])([shdc])$/i);
  if (letter) {
    const rank = letter[1] === "10" ? "T" : letter[1].toUpperCase();
    return rank + letter[2].toLowerCase();
  }
  return null;
}

/** Pretty print a card for UI/log: "Ts" → "10♠". */
export function displayCard(card) {
  const r = card[0] === "T" ? "10" : card[0];
  return r + SUIT_SYMBOL[card[1]];
}
