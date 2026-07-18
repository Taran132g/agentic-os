// Build per-player stats from a parsed PokerNow log (logparser.js output).
// Lets the backtest harness profile regulars straight from a downloaded CSV
// instead of requiring hours of live sidepanel observation first.

import {
  createStats, createHandFlags, ensureV2, recordHandStart, recordAction,
  recordCbetOpportunity, recordFacedCbet, recordFaced3bet, recordShowdown,
} from "./stats.js";

const STREETS = ["preflop", "flop", "turn", "river"];

/**
 * Accumulate opponent-model stats for every player in a parsed log.
 * @param {object[]} hands parseLog().hands
 * @returns {{[name: string]: object}} tracker.stats-shaped map keyed by display name
 */
export function statsFromLog(hands) {
  const stats = {};
  const statsFor = name => (stats[name] ??= createStats());

  for (const hand of hands) {
    const flags = {};
    const flagsFor = name => (flags[name] ??= createHandFlags());

    for (const seat of hand.seats) {
      recordHandStart(statsFor(seat.name));
    }

    // ── Preflop: raise order drives facingRaise / 3-bet / fold-to-3bet ──
    let preflopRaises = 0;
    let openRaiser = null;
    let threeBettor = null;
    for (const a of hand.streets.preflop.actions) {
      if (a.type === "post") continue;
      const facingRaise = preflopRaises >= 1;
      recordAction(statsFor(a.name), flagsFor(a.name), "preflop", a.type, facingRaise);

      // Open raiser facing a 3-bet: their next voluntary act decides fold-to-3bet
      if (threeBettor && a.name === openRaiser) {
        recordFaced3bet(statsFor(a.name), a.type === "fold");
        threeBettor = null; // only score the first response
      }
      if (a.type === "raise" || a.type === "bet") {
        preflopRaises++;
        if (preflopRaises === 1) openRaiser = a.name;
        else if (preflopRaises === 2 && a.name !== openRaiser) threeBettor = a.name;
      }
    }

    // ── Flop: c-bet opportunity for the preflop aggressor + responses ──
    const flopActions = hand.streets.flop.actions.filter(a => a.type !== "post");
    if (openRaiser && flopActions.length > 0) {
      const aggressorAct = flopActions.find(a => a.name === openRaiser);
      if (aggressorAct) {
        const made = aggressorAct.type === "bet" || aggressorAct.type === "raise";
        recordCbetOpportunity(statsFor(openRaiser), made);
        if (made) {
          // First response of each other player after the c-bet
          const cbetIdx = flopActions.indexOf(aggressorAct);
          const responded = new Set();
          for (const r of flopActions.slice(cbetIdx + 1)) {
            if (r.name === openRaiser || responded.has(r.name)) continue;
            responded.add(r.name);
            recordFacedCbet(statsFor(r.name), r.type === "fold");
          }
        }
      }
    }

    // ── Postflop aggression counters ──
    for (const street of STREETS.slice(1)) {
      for (const a of hand.streets[street].actions) {
        if (a.type === "post") continue;
        recordAction(statsFor(a.name), flagsFor(a.name), street, a.type);
      }
    }

    // ── Showdowns: what they turned over ──
    const sdStreet = hand.board.length >= 5 ? "river"
      : hand.board.length === 4 ? "turn"
      : hand.board.length === 3 ? "flop" : "preflop";
    for (const sd of hand.showdowns) {
      if (sd.cards.length === 2) {
        recordShowdown(statsFor(sd.name), sd.cards[0], sd.cards[1], sdStreet);
      }
    }
  }

  for (const s of Object.values(stats)) ensureV2(s);
  return stats;
}

/**
 * REAL results per player from consecutive hand-start stack snapshots —
 * the ground truth the backtest's simulated winrates must be judged against.
 *
 * Top-up guard: chips can only be gained by winning a pot, so a positive
 * stack delta for a player who collected nothing that hand is a rebuy/admin
 * top-up and is skipped.
 *
 * @param {object[]} hands parseLog().hands (chronological)
 * @returns {{players: {[name]: {hands: number, netBB: number, bb100: number}}, bigBlind: number, handsCounted: number}}
 */
export function realResults(hands) {
  // Session big blind = most common bb across hands
  const bbCounts = new Map();
  for (const h of hands) {
    if (h.blinds.bb > 0) bbCounts.set(h.blinds.bb, (bbCounts.get(h.blinds.bb) || 0) + 1);
  }
  let bigBlind = 0, bestN = 0;
  for (const [bb, n] of bbCounts) if (n > bestN) { bigBlind = bb; bestN = n; }
  if (bigBlind <= 0) return { players: {}, bigBlind: 0, handsCounted: 0 };

  const players = {};
  let handsCounted = 0;

  for (let i = 0; i < hands.length - 1; i++) {
    const cur = hands[i], next = hands[i + 1];
    if (!cur.seats.length || !next.seats.length) continue;
    handsCounted++;
    const winners = new Set(cur.winners.map(w => w.name));

    for (const seat of cur.seats) {
      const after = next.seats.find(s => s.id === seat.id);
      if (!after) continue; // left the table — no reliable delta
      const delta = after.stack - seat.stack;
      if (delta > 0.009 && !winners.has(seat.name)) continue; // top-up, not winnings
      const p = (players[seat.name] ??= { hands: 0, netBB: 0 });
      p.hands += 1;
      p.netBB += delta / bigBlind;
    }
  }

  for (const p of Object.values(players)) {
    p.bb100 = p.hands > 0 ? (p.netBB / p.hands) * 100 : 0;
  }
  return { players, bigBlind, handsCounted };
}
