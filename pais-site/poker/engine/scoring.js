// Decision scoring: grade a hero action against the advisor's output.
// Shared by the live sidepanel and the drill trainer. Pure ESM, no DOM.

/**
 * Map a recommendation string to the action families it accepts. Chart
 * strings can be decorated ("FOLD (TIGHT)", "CALL (TIGHTEN OOP)") or compound
 * ("ALL-IN / CALL", "LIMP BEHIND / CALL", "3-BET (BLUFF)") — exact matching
 * scores those spots wrong no matter what the user does.
 * @param {string} str
 * @returns {Set<string>} subset of {fold, call, check, raise, allin}
 */
export function actionTokens(str) {
  const s = (str || "").toUpperCase();
  const set = new Set();
  if (s.includes("FOLD"))  set.add("fold");
  if (s.includes("CALL") || s.includes("LIMP")) set.add("call");
  if (s.includes("CHECK")) set.add("check");
  if (s.includes("RAISE") || s.includes("BET") || s.includes("ALL-IN") || s.includes("JAM")) {
    set.add("raise");
    set.add("allin");
  }
  return set;
}

/**
 * Did the user's action match the advisor line? Mixed-frequency spots accept
 * either arm of the mix.
 * @param {object} advice advise() output
 * @param {string} userActionType fold|call|check|raise|allin
 */
export function isCorrect(advice, userActionType) {
  const u  = (userActionType || "").toLowerCase();
  const ok = actionTokens(advice?.recommended?.action);
  const mix = advice?.meta?.mix;
  if (mix) {
    for (const t of actionTokens(mix.primary))  ok.add(t);
    for (const t of actionTokens(mix.fallback)) ok.add(t);
  }
  return ok.has(u);
}

/**
 * BB of EV lost vs the best action. Null when EV data is unavailable
 * (preflop chart advice) or the user's action isn't in the ranked list.
 * @returns {number|null} >= 0
 */
export function computeEvLoss(advice, userActionType) {
  if (!advice?.actions || advice.actions.length === 0) return null;
  const bestEv = advice.recommended?.ev;
  if (bestEv == null) return null;
  const u = (userActionType || "").toLowerCase();
  const userAct = advice.actions.find(a => actionTokens(a.action).has(u));
  if (!userAct || userAct.ev == null) return null;
  return Math.max(0, bestEv - userAct.ev);
}

/**
 * Bucket a wrong decision into a named mistake pattern for leak tracking.
 * @returns {string|null} null when the decision was correct or unclassifiable
 */
export function classifyMistake(advice, userActionType) {
  if (isCorrect(advice, userActionType)) return null;
  const recTokens = actionTokens(advice?.recommended?.action);
  const u = (userActionType || "").toLowerCase();
  if (u === "fold" && (recTokens.has("call") || recTokens.has("check") || recTokens.has("raise"))) return "folded_good_spot";
  if ((u === "call" || u === "check") && recTokens.has("fold"))  return "called_should_fold";
  if ((u === "call" || u === "check") && recTokens.has("raise")) return "called_should_raise";
  if ((u === "raise" || u === "allin") && recTokens.has("fold"))  return "raised_should_fold";
  if ((u === "raise" || u === "allin") && (recTokens.has("call") || recTokens.has("check"))) return "raised_should_call";
  return null;
}

export const MISTAKE_LABELS = {
  folded_good_spot:    "Folding good spots",
  called_should_fold:  "Calling too loose",
  called_should_raise: "Flat-calling vs raising",
  raised_should_fold:  "Bluffing too much",
  raised_should_call:  "Over-raising vs calling",
  sizing_off:          "Bet sizing off",
};

/**
 * Sizing check for correct-action raises: right family, wrong amount is a
 * real leak (min-clicking where the advisor wanted 3x). Only fires when the
 * advisor recommended an aggressive line with a numeric size and the user
 * raised. Tolerance ±40% — sizing is an art, only flag clear deviations.
 * @param {object} advice advise() output
 * @param {number|null} userTotal the user's raise-to amount
 * @returns {{wanted: number, got: number}|null} null when sizing is fine or unscoreable
 */
export function checkSizing(advice, userTotal) {
  if (userTotal == null || !(userTotal > 0)) return null;
  const rec = advice?.recommended;
  if (!rec || !actionTokens(rec.action).has("raise")) return null;
  const wanted = typeof rec.size === "number" ? rec.size : null;
  if (wanted == null || wanted <= 0) return null;
  const ratio = userTotal / wanted;
  if (ratio >= 0.6 && ratio <= 1.4) return null;
  return { wanted, got: userTotal };
}

/**
 * Parse a tracker action line ("Taran raises 10", "Taran folds") into an
 * action token. Bets grade as the raise family — the advisor's BET/RAISE
 * recommendations map there too.
 * @returns {"fold"|"call"|"check"|"raise"|null}
 */
export function parseActionVerb(actionText, playerName) {
  return parseAction(actionText, playerName)?.verb ?? null;
}

/**
 * Parse a tracker action line into verb + amount ("Taran raises 10" →
 * {verb: "raise", amount: 10}). Amount is null for fold/check.
 */
export function parseAction(actionText, playerName) {
  if (!actionText || !playerName) return null;
  if (!actionText.startsWith(playerName + " ")) return null;
  const rest = actionText.slice(playerName.length + 1);
  const amt = () => {
    const m = rest.match(/([\d.]+)/);
    return m ? parseFloat(m[1]) : null;
  };
  if (/^folds/.test(rest))  return { verb: "fold", amount: null };
  if (/^checks/.test(rest)) return { verb: "check", amount: null };
  if (/^calls/.test(rest))  return { verb: "call", amount: amt() };
  if (/^(raises|bets)/.test(rest)) return { verb: "raise", amount: amt() };
  return null;
}

/** Fresh leak-tracking store. */
export function emptyLeaks() {
  return {
    street:   { preflop: { t: 0, c: 0 }, flop: { t: 0, c: 0 }, turn: { t: 0, c: 0 }, river: { t: 0, c: 0 } },
    position: {},
    mistake:  {},
  };
}

/** Record one graded decision into a leak store (mutates `leaks`). */
export function recordLeak(leaks, { street, position, correct, mistakeType }) {
  if (leaks.street[street]) {
    leaks.street[street].t++;
    if (correct) leaks.street[street].c++;
  }
  if (position) {
    if (!leaks.position[position]) leaks.position[position] = { t: 0, c: 0 };
    leaks.position[position].t++;
    if (correct) leaks.position[position].c++;
  }
  if (!correct && mistakeType) {
    leaks.mistake[mistakeType] = (leaks.mistake[mistakeType] || 0) + 1;
  }
}

/**
 * Summarize a leak store into display-ready findings.
 * @param {object} leaks emptyLeaks()-shaped store
 * @param {number} minSample categories below this many decisions are ignored
 */
export function leakSummary(leaks, minSample = 3) {
  const out = { weakestStreet: null, weakestPosition: null, topMistake: null };

  const streets = Object.entries(leaks.street)
    .filter(([, v]) => v.t >= minSample)
    .map(([k, v]) => ({ name: k, pct: v.c / v.t, total: v.t }))
    .sort((a, b) => a.pct - b.pct);
  if (streets.length) out.weakestStreet = streets[0];

  const positions = Object.entries(leaks.position)
    .filter(([, v]) => v.t >= minSample)
    .map(([k, v]) => ({ name: k, pct: v.c / v.t, total: v.t }))
    .sort((a, b) => a.pct - b.pct);
  if (positions.length) out.weakestPosition = positions[0];

  const mistakes = Object.entries(leaks.mistake)
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1]);
  if (mistakes.length) {
    out.topMistake = { type: mistakes[0][0], label: MISTAKE_LABELS[mistakes[0][0]] || mistakes[0][0], count: mistakes[0][1] };
  }

  return out;
}
