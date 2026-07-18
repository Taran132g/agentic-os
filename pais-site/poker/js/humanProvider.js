// play/js/humanProvider.js — Bridge between the engine's DecisionPoint and
// the action bar UI. The engine calls actionProvider(decision) which returns
// a Promise resolved when the human clicks a button.

export function createHumanBridge() {
  let _resolve = null;
  let _reject  = null;

  function waitForAction(decision) {
    return new Promise((resolve, reject) => {
      _resolve = resolve;
      _reject  = reject;
    });
  }

  function submitAction(actionInput) {
    if (_resolve) {
      const r = _resolve;
      _resolve = null;
      _reject  = null;
      r(actionInput);
    }
  }

  // Rejects the pending promise so the hand loop unblocks and restarts.
  function abort() {
    if (_reject) {
      const r = _reject;
      _resolve = null;
      _reject  = null;
      const err = new Error("session_reset");
      err.isAbort = true;
      r(err);
    }
  }

  return { waitForAction, submitAction, abort };
}
