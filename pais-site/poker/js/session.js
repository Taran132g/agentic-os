// play/js/session.js — Session state machine. Wires the engine's async driver
// to human UI input and AI decisions.

import { dealHandAsync, advisorDecision } from "../engine/simulator.js";
import { advise } from "../engine/advisor.js";
import { createLearningState } from "../engine/learning.js";
import { updateTendencies } from "../sim/tendencies.js";
import { loadSession, saveSession, appendHistory } from "./storage.js";
import { buildOpponents } from "./opponents.js";
import { mulberry32, randomSeed } from "./rng.js";

export const SESSION_DEFAULTS = {
  version: 1,
  config: {
    tableSize: 4,
    bigBlind: 1,
    startStack: 100,
    humanName: "You",
    coachMode: "off",
  },
  handNo: 0,
  buttonOffset: 0,
  human: { id: "human", name: "You", stack: 100 },
  ais: [],
  profitSeries: [],
};

export function createSession(config = {}, trainedState = null) {
  const cfg = { ...SESSION_DEFAULTS.config, ...config };
  const rng = mulberry32(randomSeed());
  const ls = trainedState || defaultLearningState();
  const ais = buildOpponents(ls, cfg.tableSize - 1, rng);
  return {
    ...SESSION_DEFAULTS,
    config: cfg,
    human: { id: "human", name: cfg.humanName, stack: cfg.startStack, learningState: createLearningState() },
    ais: ais.map(ai => ({ ...ai, stack: cfg.startStack })),
    profitSeries: [],
    handNo: 0,
    buttonOffset: 0,
  };
}

function defaultLearningState() {
  return {
    version: 1,
    tightness: 0.75,
    aggression: 0.35,
    handsLearned: 0,
    buckets: {},
    notes: [],
  };
}

export async function runHand(session, bridge, onEvent) {
  const { config, human, ais, buttonOffset } = session;
  const { bigBlind } = config;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Build seat order: human + AIs, rotated by buttonOffset
  const tableOrder = [human, ...ais];
  const n = tableOrder.length;
  const seated = Array.from({ length: n }, (_, i) => tableOrder[(i + buttonOffset) % n]);
  const humanSeatIdx = seated.findIndex(a => a.id === "human");

  // Ensure old sessions (loaded from localStorage) have a human learning state
  if (!session.human.learningState) {
    session.human.learningState = createLearningState();
  }

  // Build agents array with proper shape for the engine
  const agents = seated.map(a => ({
    ...a,
    learningState: a.learningState ?? null,
    tendencies: a.tendencies ?? {},
  }));

  const seed = randomSeed();
  const rng = mulberry32(seed);
  const coachEntries = [];
  let heroCardsShown = false;

  async function actionProvider(decision) {
    if (decision.seatIdx === humanSeatIdx) {
      // Yield to paint before computing coach advice
      await new Promise(r => setTimeout(r, 0));

      // Reveal hero's hole cards on first action (preflop deal reveal)
      if (!heroCardsShown && onEvent && decision.snap?.heroCards) {
        heroCardsShown = true;
        onEvent({ kind: "hero_cards", cards: decision.snap.heroCards, seatIdx: humanSeatIdx });
      }

      // Turn indicator — pulsing ring for human (ms=0 means "waiting")
      if (onEvent) onEvent({ kind: "turn", seatIdx: humanSeatIdx, ms: 0 });

      // Coach advice computation
      if (config.coachMode !== "off") {
        try {
          const advice = advise({ ...decision.snap, iterations: 400 });
          if (onEvent) onEvent({ kind: "coach", advice, decision });
          coachEntries.push({
            street: decision.street,
            pot: decision.snap.pot,
            toCall: decision.snap.toCall,
            recommended: advice.recommended,
            equity: advice.meta?.equity ?? null,
            actionsRanked: advice.actions || [],
          });
        } catch {}
      }

      const input = await bridge.waitForAction(decision);

      // Record what human actually did for review
      if (coachEntries.length > 0) {
        const last = coachEntries[coachEntries.length - 1];
        last.actual = input.action;
      }

      return input;
    }

    // AI: small random delay so it reads like a human game
    const delay = reducedMotion ? 0 : 300 + Math.floor(rng() * 600);
    if (onEvent) onEvent({ kind: "turn", seatIdx: decision.seatIdx, ms: delay });
    if (delay > 0) await new Promise(r => setTimeout(r, delay));
    return advisorDecision(decision);
  }

  const result = await dealHandAsync(agents, bigBlind, rng, {
    record: true,
    onEvent,
    actionProvider,
  });

  // Apply profits back to session stacks; sync human learning state
  for (let i = 0; i < agents.length; i++) {
    const id = agents[i].id;
    if (id === "human") {
      session.human.stack += result.profits[i];
      session.human.learningState = agents[i].learningState;
    } else {
      const ai = ais.find(a => a.id === id);
      if (ai) ai.stack += result.profits[i];
    }
  }

  // Update tendency counters (AIs observe the human's play)
  if (result.history) {
    updateTendencies(agents, result.history);
  }

  // Sync mutated tendencies back to all players (human needs this for coach reads)
  for (let i = 0; i < agents.length; i++) {
    const id = agents[i].id;
    if (id === "human") {
      session.human.tendencies = agents[i].tendencies;
    } else {
      const ai = session.ais.find(a => a.id === id);
      if (ai) ai.tendencies = agents[i].tendencies;
    }
  }

  // Auto-rebuy busted AIs
  for (const ai of session.ais) {
    if (ai.stack < bigBlind) {
      ai.stack = config.startStack;
      if (onEvent) onEvent({ kind: "rebuy", name: ai.name });
    }
  }

  session.handNo++;
  session.buttonOffset = (session.buttonOffset + 1) % n;

  const humanIdx = agents.findIndex(a => a.id === "human");
  const humanProfitBB = result.profits[humanIdx] / bigBlind;
  const prevTotal = session.profitSeries[session.profitSeries.length - 1] || 0;
  session.profitSeries.push(prevTotal + humanProfitBB);

  const handRecord = {
    handNo: session.handNo,
    seed,
    timestamp: Date.now(),
    bigBlind,
    seatedIds: agents.map(a => a.id),
    humanSeatIdx,
    history: result.history,
    pots: result.pots,
    profits: result.profits,
    humanProfitBB,
    coach: coachEntries,
  };

  appendHistory(handRecord);

  return { result, handRecord, humanSeatIdx, seated: agents };
}
