// play/js/opponents.js — Build AI agents from trained state with name variety.

const AI_NAMES = [
  "Doc Holliday",
  "Wild Bill",
  "Calamity Jane",
  "Jesse James",
  "Annie Oakley",
  "Billy the Kid",
  "Black Bart",
  "Belle Starr",
];

export function buildOpponents(trainedState, count, rng) {
  const agents = [];
  for (let i = 0; i < count; i++) {
    // Deep copy the trained learning state, apply small random jitter to dials
    const ls = JSON.parse(JSON.stringify(trainedState));
    ls.tightness  = Math.min(1, Math.max(0, ls.tightness  + (rng() - 0.5) * 0.15));
    ls.aggression = Math.min(1, Math.max(0, ls.aggression + (rng() - 0.5) * 0.15));
    agents.push({
      id: `ai_${i}`,
      name: AI_NAMES[i % AI_NAMES.length],
      stack: 100,
      learningState: ls,
      tendencies: {},
    });
  }
  return agents;
}
