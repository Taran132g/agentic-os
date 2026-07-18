// Chart-driven preflop advisor. GTO baseline from ranges.js charts with
// exploit deltas from opponent profiles layered on top.

import { handName } from "./cards.js";
import { parsedCharts, topPercentRange, rangePercent, chenScore } from "./ranges.js";
import { chartKeyFor } from "./positions.js";
import { mixedAction } from "./mixer.js";

/** Keep the top `fraction` of a chart (by hand strength) — tight-style trim. */
function trimChart(chartSet, fraction) {
  if (fraction >= 1) return chartSet;
  const sorted = [...chartSet].sort((a, b) => chenScore(b) - chenScore(a));
  return new Set(sorted.slice(0, Math.max(1, Math.round(sorted.length * fraction))));
}

/**
 * @typedef {object} PreflopSituation
 * @property {string[]} heroCards two cards
 * @property {string} position hero position label ("BTN", "BB", ...)
 * @property {"unopened"|"limped"|"open"|"3bet"|"4bet+"} facing
 * @property {number} [numLimpers]
 * @property {number} [openSizeBB] size of the open we face (in BB)
 * @property {object} [aggressorProfile] deriveProfile() output for the raiser
 * @property {number} [tableSize]
 */

/**
 * @param {PreflopSituation} sit
 * @returns {{action: string, sizing: string, confidence: string, reason: string, chart: string}}
 */
export function preflopAdvice(sit) {
  const name = handName(sit.heroCards[0], sit.heroCards[1]);
  const charts = parsedCharts();
  const pos = sit.position;
  const profile = sit.aggressorProfile;
  const looseAggressor = profile && profile.rangeWidthMult > 1.25;
  const tightAggressor = profile && profile.rangeWidthMult < 0.8;
  const keepFrac = sit.style ? sit.style.preflopKeepFraction : 1;
  const bluffFreq = sit.style
    ? Math.min(0.7, Math.max(0.25, 0.55 * (sit.style.aggression + 0.5)))
    : 0.55;

  // ── Nobody has raised yet ──────────────────────────────────────
  if (sit.facing === "unopened" || sit.facing === "limped") {
    const limpers = sit.numLimpers || 0;

    if (pos === "BB" && sit.facing === "limped") {
      // Option: raise strong hands, otherwise take the free flop.
      if (charts.rfi.CO.has(name)) {
        return advice("RAISE", `${4 + limpers}bb`, "high",
          `${name} is strong enough to punish ${limpers} limper(s) from the BB.`, "iso");
      }
      return advice("CHECK", "—", "high", "Take the free flop — your option in the BB.", "bb-option");
    }

    const chartKey = pos === "BB" ? "SB" : chartKeyFor(pos);
    const rfi = charts.rfi[chartKey] || charts.rfi.UTG;
    const open = rfi.has(name);

    if (limpers > 0) {
      // Iso-raise tighter than pure RFI: demand roughly the top 2/3 of the chart.
      const isoRange = intersectTop(rfi, 0.66);
      if (isoRange.has(name)) {
        return advice("RAISE (ISO)", `${3 + limpers}bb`, "high",
          `Isolate the ${limpers} limper(s) — ${name} plays great vs limp ranges.`, chartKey);
      }
      if (open && ["BTN", "SB", "BB", "CO"].includes(pos)) {
        return advice("LIMP BEHIND / CALL", "1bb", "low",
          `${name} is playable but not an iso-raise. Over-limping in position is acceptable.`, chartKey);
      }
      return advice("FOLD", "—", "high", `${name} isn't in the ${chartKey} iso range.`, chartKey);
    }

    if (open) {
      if (keepFrac < 1 && !trimChart(rfi, keepFrac).has(name)) {
        return advice("FOLD (TIGHT)", "—", "medium",
          `${name} is a standard ${chartKey} open, but sits at the bottom of the range — your tight style folds it.`, chartKey);
      }
      return advice("RAISE", "2.5bb", "high",
        `${name} is a standard ${chartKey} open (${rangePercent(rfi).toFixed(0)}% range).`, chartKey);
    }
    return advice("FOLD", "—", "high",
      `${name} is below the ${chartKey} opening range.`, chartKey);
  }

  // ── Facing an open raise ──────────────────────────────────────
  if (sit.facing === "open") {
    const defend = pos === "BB" ? charts.bbDefend : charts.vsRfi;
    const sizeMult = pos === "BB" || pos === "SB" ? 4 : 3; // OOP 3bets bigger
    const threeBetTo = Math.round((sit.openSizeBB || 2.5) * sizeMult * 10) / 10;

    const seed = [name, pos, "vs-open"];

    if (defend.threebetValue.has(name)) {
      return advice("3-BET", `${threeBetTo}bb`, "high",
        `${name} is a value 3-bet from ${pos}.` + exploitNote(looseAggressor, tightAggressor, "value"), "3bet-value");
    }

    // Measured exploit: the opener folds to 3-bets too often → attack with
    // hands that would otherwise just call.
    const foldsTo3bets = profile?.foldTo3bet !== null && profile?.foldTo3bet !== undefined && profile.foldTo3bet > 65;
    if (foldsTo3bets && defend.call.has(name) && !defend.threebetValue.has(name)) {
      const mix = mixedAction("3-BET (EXPLOIT)", "CALL", 0.5, seed);
      return advice(mix.chosen, mix.chosen.includes("3-BET") ? `${threeBetTo}bb` : "—", "medium",
        `Opener folds to ${profile.foldTo3bet.toFixed(0)}% of 3-bets — attack at frequency. Mix: ${mix.display}.`,
        "exploit", mix);
    }

    if (defend.threebetBluff.has(name)) {
      // Vs tight openers, drop the bluff 3bets; vs loose, keep them.
      if (tightAggressor) {
        return advice(defend.call.has(name) ? "CALL" : "FOLD", "—", "medium",
          `${name} is a 3-bet bluff candidate, but the opener is tight — downgrade.`, "3bet-bluff");
      }
      // Mixed frequency so the bluff 3-bets aren't face-up either;
      // frequency scales with the learned aggression dial.
      const fallback = defend.call.has(name) ? "CALL" : "FOLD";
      const mix = mixedAction("3-BET (BLUFF)", fallback, bluffFreq, seed);
      return advice(mix.chosen, mix.chosen.includes("3-BET") ? `${threeBetTo}bb` : "—", "medium",
        `${name} is a 3-bet bluff at frequency — blockers + playability. Mix: ${mix.display}.` +
        exploitNote(looseAggressor, tightAggressor, "bluff"), "3bet-bluff", mix);
    }
    if (defend.call.has(name)) {
      if (keepFrac < 1 && !trimChart(defend.call, keepFrac).has(name)) {
        return advice("FOLD (TIGHT)", "—", "medium",
          `${name} is a marginal defend — your tight style lets the bottom of the calling range go.`, "vs-open");
      }
      if (pos === "SB") {
        return advice("CALL (TIGHTEN OOP)", "—", "medium",
          `${name} is a defend, but SB flats play poorly — prefer 3-bet-or-fold at the margin.`, "vs-open");
      }
      return advice("CALL", "—", "high",
        `${name} defends profitably vs a ${sit.openSizeBB || 2.5}bb open.` +
        (looseAggressor ? " Loose opener → your call is extra profitable." : ""), "vs-open");
    }
    if (looseAggressor && charts.bbDefend.call.has(name) && pos === "BB") {
      return advice("CALL (EXPLOIT)", "—", "low",
        `Marginal, but the opener is loose (${profile.label}) — widen BB defense.`, "exploit");
    }
    return advice("FOLD", "—", "high", `${name} doesn't defend vs an open from ${pos}.`, "vs-open");
  }

  // ── We opened, villain 3-bet ──────────────────────────────────
  if (sit.facing === "3bet") {
    if (charts.vs3bet.fourbetValue.has(name)) {
      return advice("4-BET", "2.3x the 3-bet", "high", `${name} is a value 4-bet.`, "vs-3bet");
    }
    if (charts.vs3bet.fourbetBluff.has(name) && !tightAggressor) {
      return advice("4-BET (BLUFF)", "2.3x the 3-bet", "low",
        `${name} blocks aces — standard 4-bet bluff at low frequency.`, "vs-3bet");
    }
    if (charts.vs3bet.call.has(name)) {
      return advice("CALL", "—", "medium",
        `${name} continues vs a 3-bet.` + (tightAggressor ? " Tight 3-bettor — proceed carefully postflop." : ""), "vs-3bet");
    }
    return advice("FOLD", "—", "high",
      `${name} doesn't continue vs a 3-bet.` + (looseAggressor ? " (Vs a maniac you may widen calls slightly.)" : ""), "vs-3bet");
  }

  // ── Facing 4-bet or bigger ────────────────────────────────────
  if (["KK", "AA"].includes(name) || name === "AKs") {
    return advice("ALL-IN / CALL", "stack", "high", `${name} never folds vs 4-bet+ at 100bb.`, "vs-4bet");
  }
  if (["QQ", "AKo"].includes(name)) {
    return advice("CALL / JAM vs SMALL", "—", "medium",
      `${name} is at the bottom of the continue range vs a 4-bet — stack depth decides.`, "vs-4bet");
  }
  return advice("FOLD", "—", "high", `${name} is a clear fold vs 4-bet+.`, "vs-4bet");
}

function advice(action, sizing, confidence, reason, chart, mix = null) {
  return { action, sizing, confidence, reason, chart, mix };
}

function exploitNote(loose, tight, kind) {
  if (loose && kind === "value") return " Loose opener → widen value (add TT, AQs).";
  if (tight && kind === "value") return " Tight opener → their continue range is strong; play fit-or-fold postflop.";
  if (loose && kind === "bluff") return " They open wide — expect folds often.";
  return "";
}

/** Intersect a chart with the top X (0-1) fraction of hands overall. */
function intersectTop(chartSet, fraction) {
  const pct = rangePercent(chartSet) * fraction;
  const top = topPercentRange(pct);
  return new Set([...chartSet].filter(n => top.has(n)));
}
