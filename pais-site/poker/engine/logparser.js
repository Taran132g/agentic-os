// Parser for PokerNow's exported hand-history CSV ("Log/Ledger" download).
// File is reverse-chronological with columns entry,at,order. Player tokens
// appear as "name @ id" — we key players by the id and display the name.

import { parseCardToken } from "./cards.js";

/** Minimal CSV line splitter for the 3-column PokerNow format. */
function splitCsvLine(line) {
  const fields = [];
  let cur = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuotes) {
      if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; }
      else if (ch === '"') inQuotes = false;
      else cur += ch;
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      fields.push(cur); cur = "";
    } else {
      cur += ch;
    }
  }
  fields.push(cur);
  return fields;
}

/** "name @ id" → {name, id}; PokerNow doubles quotes inside the entry field. */
function parsePlayerToken(str) {
  const m = str.match(/^(.*) @ ([^ ]+)$/);
  return m ? { name: m[1], id: m[2] } : { name: str, id: str };
}

function parseCards(str) {
  return str.split(",").map(t => parseCardToken(t.trim())).filter(Boolean);
}

/**
 * Parse a full PokerNow CSV export.
 * @param {string} csvText
 * @returns {{hands: object[], players: Map<string,string>}} hands in
 *   chronological order; players maps id → display name.
 */
export function parseLog(csvText) {
  const lines = csvText.split(/\r?\n/).filter(l => l.trim());
  const entries = [];
  for (const line of lines.slice(0, 1)[0]?.startsWith("entry") ? lines.slice(1) : lines) {
    const [entry, at, order] = splitCsvLine(line);
    if (entry !== undefined && order !== undefined) {
      entries.push({ entry, at, order: order.trim() });
    }
  }
  entries.sort((a, b) => (a.order < b.order ? -1 : a.order > b.order ? 1 : 0));

  const hands = [];
  const players = new Map();
  let hand = null;

  const pushAction = (street, action) => {
    if (!hand) return;
    hand.streets[street].actions.push(action);
  };

  for (const { entry } of entries) {
    let m;

    // Hand boundaries. Older exports parenthesize the game name
    // ("(No Limit Texas Hold'em)"), 2026-era exports write it bare —
    // accept both by matching lazily up to the dealer/dead-button token.
    m = entry.match(/^-- starting hand #(\d+)\s+\(id: ([^)]+)\)\s+(.*?)\s*\(dealer: "(.+?) @ ([^"]+)"\)/);
    if (m) {
      hand = {
        number: Number(m[1]),
        id: m[2],
        game: m[3].replace(/^\(|\)$/g, ""),
        dealer: { name: m[4], id: m[5] },
        seats: [],
        blinds: { sb: 0, bb: 0 },
        heroCards: null,
        board: [],
        streets: {
          preflop: { actions: [] },
          flop: { actions: [], cards: [] },
          turn: { actions: [], card: null },
          river: { actions: [], card: null }
        },
        showdowns: [],
        winners: []
      };
      continue;
    }
    // Dead-button variant: (dealer: missing) or dead button lines
    m = entry.match(/^-- starting hand #(\d+)\s+\(id: ([^)]+)\)\s+(.*?)\s*\(dead button\)/);
    if (m) {
      hand = {
        number: Number(m[1]), id: m[2], game: m[3].replace(/^\(|\)$/g, ""), dealer: null,
        seats: [], blinds: { sb: 0, bb: 0 }, heroCards: null, board: [],
        streets: {
          preflop: { actions: [] }, flop: { actions: [], cards: [] },
          turn: { actions: [], card: null }, river: { actions: [], card: null }
        },
        showdowns: [], winners: []
      };
      continue;
    }
    if (/^-- ending hand #/.test(entry)) {
      if (hand) hands.push(hand);
      hand = null;
      continue;
    }
    if (!hand) continue;

    // Seats + stacks
    if (entry.startsWith("Player stacks:")) {
      const seatRe = /#(\d+) "(.+?) @ ([^"]+)" \(([\d.]+)\)/g;
      let sm;
      while ((sm = seatRe.exec(entry))) {
        hand.seats.push({ seat: Number(sm[1]), name: sm[2], id: sm[3], stack: Number(sm[4]) });
        players.set(sm[3], sm[2]);
      }
      continue;
    }

    // Hero cards
    m = entry.match(/^Your hand is (.+)$/);
    if (m) { hand.heroCards = parseCards(m[1]); continue; }

    // Board
    m = entry.match(/^Flop:\s+\[(.+)\]$/);
    if (m) { hand.streets.flop.cards = parseCards(m[1]); hand.board = [...hand.streets.flop.cards]; continue; }
    m = entry.match(/^Turn: .*\[(.+)\]$/);
    if (m) { hand.streets.turn.card = parseCardToken(m[1]); hand.board.push(hand.streets.turn.card); continue; }
    m = entry.match(/^River: .*\[(.+)\]$/);
    if (m) { hand.streets.river.card = parseCardToken(m[1]); hand.board.push(hand.streets.river.card); continue; }

    // Blinds / straddles
    m = entry.match(/^"(.+?) @ ([^"]+)" posts a (small blind|big blind|missing small blind|straddle) of ([\d.]+)/);
    if (m) {
      const amount = Number(m[4]);
      if (m[3] === "small blind") hand.blinds.sb = amount;
      if (m[3] === "big blind") hand.blinds.bb = amount;
      pushAction("preflop", { name: m[1], id: m[2], type: "post", amount, blind: m[3] });
      continue;
    }

    // Player actions — street inferred from board progress
    const street = hand.streets.river.card ? "river"
      : hand.streets.turn.card ? "turn"
      : hand.streets.flop.cards.length ? "flop" : "preflop";

    m = entry.match(/^"(.+?) @ ([^"]+)" (folds|checks)/);
    if (m) { pushAction(street, { name: m[1], id: m[2], type: m[3] === "folds" ? "fold" : "check" }); continue; }

    m = entry.match(/^"(.+?) @ ([^"]+)" calls ([\d.]+)( and go all in)?/);
    if (m) { pushAction(street, { name: m[1], id: m[2], type: "call", amount: Number(m[3]), allIn: !!m[4] }); continue; }

    m = entry.match(/^"(.+?) @ ([^"]+)" bets ([\d.]+)( and go all in)?/);
    if (m) { pushAction(street, { name: m[1], id: m[2], type: "bet", amount: Number(m[3]), allIn: !!m[4] }); continue; }

    m = entry.match(/^"(.+?) @ ([^"]+)" raises to ([\d.]+)( and go all in)?/);
    if (m) { pushAction(street, { name: m[1], id: m[2], type: "raise", amount: Number(m[3]), allIn: !!m[4] }); continue; }

    // Showdown / results
    m = entry.match(/^"(.+?) @ ([^"]+)" shows a (.+)\.$/);
    if (m) { hand.showdowns.push({ name: m[1], id: m[2], cards: parseCards(m[3]) }); continue; }

    m = entry.match(/^"(.+?) @ ([^"]+)" collected ([\d.]+) from pot( with (.+?) \(combination: (.+)\))?/);
    if (m) {
      hand.winners.push({
        name: m[1], id: m[2], amount: Number(m[3]),
        handName: m[5] || null, combination: m[6] ? parseCards(m[6]) : null
      });
      continue;
    }
  }
  if (hand) hands.push(hand); // trailing unterminated hand

  return { hands, players };
}

/**
 * Try to identify the hero seat: the player whose showdown cards match
 * "Your hand is" lines. Returns id or null.
 */
export function guessHeroId(hands) {
  for (const hand of hands) {
    if (!hand.heroCards || hand.heroCards.length !== 2) continue;
    const key = [...hand.heroCards].sort().join();
    for (const sd of hand.showdowns) {
      if (sd.cards.length === 2 && [...sd.cards].sort().join() === key) return sd.id;
    }
  }
  return null;
}
