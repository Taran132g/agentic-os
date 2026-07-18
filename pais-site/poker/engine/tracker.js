// HandTracker: consumes successive scraped table states (from the content
// script poll) and maintains per-hand context — preflop roles, action
// counts, blind inference, opponent tendency events (c-bets, check-raises,
// fold-to-pressure, showdown reveals) and long-run stats. Pure module so it
// can be tested against recorded state sequences.

import {
  createStats, createHandFlags, recordHandStart, recordAction, deriveProfile,
  recordCbetOpportunity, recordFacedCbet, recordFaced3bet, recordCheckRaise,
  recordShowdown, ensureV2
} from "./stats.js";
import { assignPositions } from "./positions.js";

export class HandTracker {
  constructor(persistedStats = {}) {
    this.stats = persistedStats; // name → stats (persisted by caller)
    for (const s of Object.values(this.stats)) ensureV2(s);
    this.reset();
    this.bigBlind = 0;
    this.handCount = 0;
  }

  reset() {
    this.flags = {};        // name → per-hand flags
    this.roles = {};        // name → preflop role
    this.postflop = {};     // name → {aggressions, calls}
    this.positions = new Map();
    this.raisesSeen = 0;    // preflop raise count (1 = open, 2 = 3bet...)
    this.prevBets = {};     // name → last seen bet on current street
    this.prevStreet = "preflop";
    this.dealtNames = [];
    // v2 per-hand state
    this.openRaiser = null;         // first preflop raiser
    this.lastPreflopRaiser = null;  // has the initiative postflop
    this.facing3bet = null;         // open raiser currently facing a 3bet
    this.streetChecks = {};         // name → checked this street
    this.flopCbetBy = null;         // who c-bet the flop
    this.cbetOppResolved = false;   // aggressor's first flop action seen
    this.cbetFacedResolved = new Set(); // players who responded to the c-bet
    this.shownRecorded = new Set(); // players whose showdown we recorded
  }

  statsFor(name) {
    if (!this.stats[name]) this.stats[name] = createStats();
    return ensureV2(this.stats[name]);
  }

  flagsFor(name) {
    if (!this.flags[name]) this.flags[name] = createHandFlags();
    return this.flags[name];
  }

  postflopFor(name) {
    if (!this.postflop[name]) this.postflop[name] = { aggressions: 0, calls: 0 };
    return this.postflop[name];
  }

  /** Detect a fresh hand: board cleared and pot reset (or hero cards changed). */
  isNewHand(state, prev) {
    if (!prev) return true;
    const boardCleared = prev.boardCards.length > 0 && state.boardCards.length === 0;
    const potReset = prev.pot > state.pot && state.pot <= (this.bigBlind * 3 || 10);
    const heroPrev = prev.players.find(p => p.isHero);
    const heroNow = state.players.find(p => p.isHero);
    const cardsChanged = heroPrev?.cards?.length === 2 && heroNow?.cards?.length === 2 &&
      heroPrev.cards.join() !== heroNow.cards.join();
    return boardCleared || (state.boardCards.length === 0 && (potReset || cardsChanged));
  }

  /** Infer the big blind from forced bets at the start of a hand. */
  inferBlinds(state) {
    const bets = state.players.map(p => p.bet).filter(b => b > 0).sort((a, b) => a - b);
    if (bets.length >= 2 && bets[bets.length - 1] >= bets[0] * 2 - 0.01) {
      this.bigBlind = bets[bets.length - 1];
    } else if (bets.length === 1 && !this.bigBlind) {
      this.bigBlind = bets[0];
    }
  }

  /**
   * Feed the next polled state. Returns events for the UI log.
   * @returns {{newHand: boolean, streetChanged: boolean, actions: string[]}}
   */
  update(state, prev) {
    const events = { newHand: false, streetChanged: false, actions: [] };

    if (this.isNewHand(state, prev)) {
      this.reset();
      events.newHand = true;
      this.handCount += 1;
      this.inferBlinds(state);

      const dealt = state.players.filter(p => !p.sittingOut);
      this.dealtNames = dealt.map(p => p.name);
      const dealerSeat = dealt.find(p => p.isDealer)?.seatIndex;
      if (dealerSeat !== undefined) {
        this.positions = assignPositions(dealt, dealerSeat);
      }
      for (const p of dealt) recordHandStart(this.statsFor(p.name));

      // Blinds are already posted in the first state of a hand. Seed the
      // bet baseline with them so the diff loop below never records forced
      // posts as voluntary calls (2 false VPIPs/hand ≈ +22pts inflation).
      for (const p of state.players) this.prevBets[p.name] = p.bet;
      this.detectShowdowns(state);
      this.prevStreet = state.street;
      return events;
    }

    // Dealer button animates in after the deal — if the hand-start tick
    // missed it, positions stayed empty all hand and blind detection broke.
    // Retry until the button shows up.
    if (this.positions.size === 0) {
      const dealt = state.players.filter(p => !p.sittingOut);
      const dealerSeat = dealt.find(p => p.isDealer)?.seatIndex;
      if (dealerSeat !== undefined) {
        this.positions = assignPositions(dealt, dealerSeat);
      }
    }

    if (prev && state.street !== prev.street && !events.newHand) {
      events.streetChanged = true;
      this.prevBets = {};       // bets reset each street on PokerNow
      this.streetChecks = {};
    }

    // Diff each player's bet/fold state to derive actions.
    for (const p of state.players) {
      const prevP = prev?.players?.find(q => q.name === p.name);
      const prevBet = this.prevBets[p.name] ?? prevP?.bet ?? 0;

      if (p.folded && prevP && !prevP.folded) {
        this.noteAction(state, p, "fold");
        events.actions.push(`${p.name} folds`);
      } else if (p.bet > prevBet) {
        const maxOther = Math.max(0, ...state.players.filter(q => q.name !== p.name).map(q => q.bet));
        const type = maxOther === 0 && state.street !== "preflop" ? "bet"
          : p.bet > maxOther + 1e-9 ? "raise" : "call";
        // Blind posts are not actions — identified by position + exact size.
        // When the position is unknown (dealer button never detected), any
        // unraised first bet of exactly SB/BB size is treated as a post:
        // under-counting the odd limp beats inflating every blind to a call.
        const pos = this.positions.get(p.name);
        const sizeIsBB = Math.abs(p.bet - this.bigBlind) < 0.01;
        const sizeIsSB = Math.abs(p.bet - this.bigBlind / 2) < 0.01;
        const isBlindPost = state.street === "preflop" && this.raisesSeen === 0 && prevBet === 0 &&
          (pos ? ((pos === "BB" && sizeIsBB) || (pos === "SB" && sizeIsSB))
               : (sizeIsBB || sizeIsSB));
        if (!isBlindPost) {
          this.noteAction(state, p, type);
          events.actions.push(`${p.name} ${type}s ${p.bet}`);
        }
      } else if (
        // Check detection: current turn moved past them with no bet change.
        prevP && !p.folded && p.bet === prevBet &&
        prevP.isCurrentTurn && !p.isCurrentTurn && state.street !== "preflop"
      ) {
        this.noteAction(state, p, "check");
        events.actions.push(`${p.name} checks`);
      }
      this.prevBets[p.name] = p.bet;
    }

    this.detectShowdowns(state);
    this.prevStreet = state.street;
    return events;
  }

  noteAction(state, player, type) {
    const facingRaise = this.raisesSeen >= 1;
    recordAction(this.statsFor(player.name), this.flagsFor(player.name), state.street, type, facingRaise);

    if (state.street === "preflop") {
      this.notePreflop(player, type);
    } else {
      this.notePostflop(state, player, type);
    }
  }

  notePreflop(player, type) {
    if (type === "raise") {
      this.raisesSeen += 1;
      if (this.raisesSeen === 1) {
        this.openRaiser = player.name;
        this.roles[player.name] = "raiser";
      } else {
        this.roles[player.name] = "3bettor";
        // The open raiser is now facing a 3bet — watch their response.
        if (this.raisesSeen === 2 && this.openRaiser && this.openRaiser !== player.name) {
          this.facing3bet = this.openRaiser;
        }
      }
      this.lastPreflopRaiser = player.name;
    } else if (type === "call") {
      if (!this.roles[player.name]) {
        this.roles[player.name] = this.raisesSeen >= 1 ? "caller" : "limper";
      }
    }

    // Open raiser responded to the 3bet?
    if (this.facing3bet === player.name && (type === "fold" || type === "call" || type === "raise")) {
      recordFaced3bet(this.statsFor(player.name), type === "fold");
      this.facing3bet = null;
    }
  }

  notePostflop(state, player, type) {
    const pf = this.postflopFor(player.name);
    if (type === "bet" || type === "raise") pf.aggressions += 1;
    else if (type === "call") pf.calls += 1;

    // Check-raise: they checked earlier this street, now raise.
    if (type === "raise" && this.streetChecks[player.name]) {
      recordCheckRaise(this.statsFor(player.name));
    }
    if (type === "check") this.streetChecks[player.name] = true;

    if (state.street === "flop") {
      // C-bet opportunity: preflop aggressor's first flop action.
      if (!this.cbetOppResolved && player.name === this.lastPreflopRaiser) {
        this.cbetOppResolved = true;
        const made = type === "bet" || type === "raise";
        recordCbetOpportunity(this.statsFor(player.name), made);
        if (made) this.flopCbetBy = player.name;
      } else if (
        // Response to the c-bet: first reaction from each other player.
        this.flopCbetBy && player.name !== this.flopCbetBy &&
        !this.cbetFacedResolved.has(player.name) &&
        (type === "fold" || type === "call" || type === "raise")
      ) {
        this.cbetFacedResolved.add(player.name);
        recordFacedCbet(this.statsFor(player.name), type === "fold");
      }
    }
  }

  /** Record revealed villain holdings (showdown or all-in runout). */
  detectShowdowns(state) {
    for (const p of state.players) {
      if (p.isHero || p.folded || this.shownRecorded.has(p.name)) continue;
      if (p.cards?.length === 2 && state.boardCards.length >= 3) {
        this.shownRecorded.add(p.name);
        recordShowdown(this.statsFor(p.name), p.cards[0], p.cards[1], state.street);
      }
    }
  }

  /** Build the advisor's villain descriptors for the current live opponents. */
  villainsFor(state) {
    return state.players
      .filter(p => !p.isHero && !p.folded)
      .map(p => {
        const role = this.roles[p.name] ||
          (this.positions.get(p.name) === "BB" && state.street !== "preflop" ? "blind-check" : "unknown");
        const pf = this.postflopFor(p.name);
        return {
          name: p.name,
          position: this.positions.get(p.name) || "CO",
          stack: p.stack,
          bet: p.bet,
          preflopRole: role,
          postflopAggressions: pf.aggressions,
          postflopCalls: pf.calls,
          checkedThisStreet: !!this.streetChecks[p.name],
          profile: deriveProfile(this.stats[p.name]),
          rawStats: this.stats[p.name] || null
        };
      });
  }

  /** What is hero facing preflop right now? */
  preflopFacing() {
    if (this.raisesSeen === 0) {
      return Object.values(this.roles).includes("limper") ? "limped" : "unopened";
    }
    if (this.raisesSeen === 1) return "open";
    if (this.raisesSeen === 2) return "3bet";
    return "4bet+";
  }
}
