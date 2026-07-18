// sim/tendencies.js — Shared per-opponent tendency tracking for all sim
// harnesses (run/loop/bench). Each agent keeps counters per opponent ID that
// deriveSimProfile() in the engine converts to an exploit profile.
//
// Preflop folds are deliberately NOT counted: everyone folds most hands
// preflop, so counting them made every agent read as a FOLDER (inflating
// fold-equity estimates across the board). Fold counts here mean
// "folds when involved" — the postflop signal the profile actually needs.

export function updateTendencies(seated, history) {
  // Increment hand count between every pair at the table.
  for (let si = 0; si < seated.length; si++) {
    for (let oi = 0; oi < seated.length; oi++) {
      if (si === oi) continue;
      const tgt = seated[oi].id;
      if (!seated[si].tendencies[tgt])
        seated[si].tendencies[tgt] = { hands: 0, jams: 0, folds: 0, raises: 0, calls: 0, actions: 0 };
      seated[si].tendencies[tgt].hands++;
    }
  }
  // Tally each observed action.
  for (const street of history.streets) {
    for (const action of street.actions) {
      if (action.type === "blind") continue;
      if (street.name === "preflop" && action.type === "fold") continue;
      const actorId = seated[action.playerIdx]?.id;
      if (actorId === undefined) continue;
      for (let si = 0; si < seated.length; si++) {
        if (si === action.playerIdx) continue;
        const t = seated[si].tendencies[actorId]
          ?? (seated[si].tendencies[actorId] = { hands: 0, jams: 0, folds: 0, raises: 0, calls: 0, actions: 0 });
        t.actions++;
        if      (action.type === "allin") t.jams++;
        else if (action.type === "fold")  t.folds++;
        else if (action.type === "raise") t.raises++;
        else if (action.type === "call")  t.calls++;
      }
    }
  }
}
