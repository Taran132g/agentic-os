// Anti-face-up layer. Two jobs:
//  1. Mixed strategies — GTO plays marginal hands at frequencies, not
//     always the same way. A deterministic per-decision roll picks the
//     branch so the panel doesn't flicker, but across hands you're mixed.
//  2. Hero balance meter — watches YOUR OWN frequencies and warns when
//     your line becomes readable (the actual "playing face up" problem).

/** Deterministic hash → [0,1) so the same decision always rolls the same. */
export function decisionRoll(seedParts) {
  const str = seedParts.join("|");
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) / 4294967296;
}

/**
 * Mix two actions at a frequency.
 * @param {string} primary action taken `freq` of the time
 * @param {string} fallback action otherwise
 * @param {number} freq 0-1
 * @param {string[]} seedParts identifies this decision (hand cards, street, board)
 */
export function mixedAction(primary, fallback, freq, seedParts) {
  const roll = decisionRoll(seedParts);
  const chosen = roll < freq ? primary : fallback;
  return {
    chosen,
    primary,
    fallback,
    freq,
    roll,
    display: `${primary} ${(freq * 100).toFixed(0)}% / ${fallback} ${((1 - freq) * 100).toFixed(0)}%`
  };
}

/**
 * Analyze HERO's own stats for exploitable patterns.
 * @param {object} heroStats stats.js object for the hero
 * @returns {{score: number, warnings: string[]}} score 0 (balanced) → 100 (face up)
 */
export function balanceReport(heroStats) {
  const warnings = [];
  let score = 0;

  if (!heroStats || heroStats.hands < 10) {
    return { score: 0, warnings: ["Balance meter needs ~10 hands of your own play."] };
  }

  const vpip = (heroStats.vpip / heroStats.hands) * 100;
  const pfr = (heroStats.pfr / heroStats.hands) * 100;
  const cbet = heroStats.cbetOpp >= 5 ? (heroStats.cbetMade / heroStats.cbetOpp) * 100 : null;
  const foldToCbet = heroStats.foldToCbetOpp >= 5 ? (heroStats.foldToCbet / heroStats.foldToCbetOpp) * 100 : null;
  const totalPostflop = heroStats.aggActions + heroStats.passiveActions;
  const af = totalPostflop >= 8 ? heroStats.aggActions / Math.max(1, heroStats.passiveActions) : null;

  if (vpip - pfr > 12) {
    score += 20;
    warnings.push(`You limp/call a lot (VPIP ${vpip.toFixed(0)} vs PFR ${pfr.toFixed(0)}) — your raises scream strength. Raise more of your playable hands.`);
  }
  if (cbet !== null && cbet > 80) {
    score += 20;
    warnings.push(`You c-bet ${cbet.toFixed(0)}% — observant villains will float/raise you. Check some strong hands too.`);
  }
  if (cbet !== null && cbet < 35) {
    score += 15;
    warnings.push(`You c-bet only ${cbet.toFixed(0)}% — when you do bet, it's face-up value. Add bluff c-bets on dry boards.`);
  }
  if (foldToCbet !== null && foldToCbet > 65) {
    score += 20;
    warnings.push(`You fold to ${foldToCbet.toFixed(0)}% of c-bets — you're printing money for anyone who bets. Defend with pairs+draws.`);
  }
  if (af !== null && af < 0.8) {
    score += 15;
    warnings.push("Your postflop line is call/check heavy — bets you DO make are transparent. Mix in raises with draws.");
  }
  if (af !== null && af > 4) {
    score += 10;
    warnings.push("Hyper-aggressive line — good players will trap you. Slow down on dangerous boards.");
  }
  if (heroStats.checkRaises === 0 && heroStats.hands > 25) {
    score += 10;
    warnings.push("You've never check-raised — your checks are face-up weakness. Check-raise some strong hands OOP.");
  }

  if (warnings.length === 0) warnings.push("Frequencies look balanced — hard to read.");
  return { score: Math.min(100, score), warnings };
}
