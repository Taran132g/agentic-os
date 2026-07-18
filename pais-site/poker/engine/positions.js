// Position assignment from the dealer button. PokerNow renders seats in
// clockwise DOM order, so seat index + dealer index fully determine position.

/** Position labels in order starting FROM the dealer, per table size. */
const LABELS_BY_COUNT = {
  2: ["BTN", "BB"], // heads-up: dealer is SB/BTN
  3: ["BTN", "SB", "BB"],
  4: ["BTN", "SB", "BB", "CO"],
  5: ["BTN", "SB", "BB", "UTG", "CO"],
  6: ["BTN", "SB", "BB", "UTG", "HJ", "CO"],
  7: ["BTN", "SB", "BB", "UTG", "UTG+1", "HJ", "CO"],
  8: ["BTN", "SB", "BB", "UTG", "UTG+1", "UTG+2", "HJ", "CO"],
  9: ["BTN", "SB", "BB", "UTG", "UTG+1", "UTG+2", "MP", "HJ", "CO"],
  10: ["BTN", "SB", "BB", "UTG", "UTG+1", "UTG+2", "MP", "MP+1", "HJ", "CO"]
};

/** Which RFI chart a position maps to (early seats collapse to UTG). */
const CHART_KEY = {
  BTN: "BTN", SB: "SB", BB: "BB", CO: "CO", HJ: "HJ",
  UTG: "UTG", "UTG+1": "UTG", "UTG+2": "UTG", MP: "HJ", "MP+1": "HJ"
};

/**
 * Assign a position label to every non-sitting-out player.
 * @param {{name: string, seatIndex: number, isDealer?: boolean}[]} players
 *   Players dealt into the hand, in seat (DOM) order.
 * @param {number} dealerSeatIndex seatIndex of the button
 * @returns {Map<string, string>} player name → position label
 */
export function assignPositions(players, dealerSeatIndex) {
  const n = players.length;
  const out = new Map();
  if (n < 2) return out;
  const labels = LABELS_BY_COUNT[Math.min(n, 10)];

  const dealerIdx = players.findIndex(p => p.seatIndex === dealerSeatIndex);
  if (dealerIdx === -1) return out;

  for (let offset = 0; offset < n; offset++) {
    const player = players[(dealerIdx + offset) % n];
    out.set(player.name, labels[offset]);
  }
  return out;
}

/** Map any position label to its RFI chart key ("BB" has no RFI chart). */
export function chartKeyFor(position) {
  return CHART_KEY[position] || "UTG";
}

/**
 * Relative order of preflop action for sorting/UI. Lower acts earlier.
 * Preflop: UTG first, BB last. Postflop: SB first, BTN last.
 */
export function preflopActOrder(position, tableSize) {
  const labels = LABELS_BY_COUNT[Math.min(Math.max(tableSize, 2), 10)];
  const idx = labels.indexOf(position);
  if (idx === -1) return 99;
  // labels start at BTN; preflop action starts at seat after BB (offset 3)
  return (idx - 3 + labels.length) % labels.length;
}

/** True if `pos` acts after `otherPos` postflop (i.e. has position). */
export function hasPositionOn(pos, otherPos, tableSize) {
  const labels = LABELS_BY_COUNT[Math.min(Math.max(tableSize, 2), 10)];
  // Postflop action order: SB, BB, UTG, ..., BTN — i.e. label index order
  // rotated so SB (index 1) is first and BTN (index 0) is last.
  const postflopRank = p => {
    const idx = labels.indexOf(p);
    return idx === 0 ? labels.length : idx; // BTN acts last
  };
  return postflopRank(pos) > postflopRank(otherPos);
}
