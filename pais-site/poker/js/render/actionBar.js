// play/js/render/actionBar.js — Action bar: merged Check/Call, log-curve raise
// slider, keyboard shortcuts, live hand-strength label.

import { soundFold, soundChip, soundClick } from "../audio.js";
import { evaluate, CATEGORY_NAME } from "../../engine/evaluator.js";

const LOG_FACTOR = 2.8; // controls how aggressively the slider curves

function logToFrac(t) {
  return (Math.exp(t * LOG_FACTOR) - 1) / (Math.exp(LOG_FACTOR) - 1);
}

function fracToLog(frac) {
  if (frac <= 0) return 0;
  return Math.log(frac * (Math.exp(LOG_FACTOR) - 1) + 1) / LOG_FACTOR;
}

export function createActionBar(rootEl, bridge) {
  const btnFold      = document.getElementById("btnFold");
  const btnCheckCall = document.getElementById("btnCheckCall");
  const btnRaise     = document.getElementById("btnRaise");
  const raiseAmt     = document.getElementById("raiseAmt");
  const raiseSlider  = document.getElementById("raiseSlider");
  const raiseInput   = document.getElementById("raiseInput");
  const abFacing     = document.getElementById("abFacing");
  const abWaiting    = document.getElementById("abWaiting");
  const abSizing     = document.getElementById("abSizing");
  const abStrength   = document.getElementById("abStrength");
  const presets      = rootEl.querySelectorAll("[data-preset]");

  let currentDecision = null;
  let currentRaiseTo  = 0;
  let currentBigBlind = 1;
  let checkCallMode   = "check"; // "check" | "call"

  function setDisabled(disabled) {
    for (const btn of [btnFold, btnCheckCall, btnRaise]) {
      if (btn) btn.disabled = disabled;
    }
    for (const btn of presets) btn.disabled = disabled;
    if (raiseSlider) raiseSlider.disabled = disabled;
    if (raiseInput)  raiseInput.disabled  = disabled;
  }

  function clampRaise(val, legal) {
    return Math.min(legal.maxRaiseTo, Math.max(legal.minRaiseTo, val));
  }

  function updateRaiseDisplay(raiseTo, legal, bigBlind) {
    currentRaiseTo = raiseTo;
    if (raiseAmt)   raiseAmt.textContent = (raiseTo / bigBlind).toFixed(1) + "bb";
    if (raiseInput) raiseInput.value = (raiseTo / bigBlind).toFixed(1);
    const range = legal.maxRaiseTo - legal.minRaiseTo;
    const frac  = range > 0.001 ? (raiseTo - legal.minRaiseTo) / range : 0;
    if (raiseSlider) raiseSlider.value = Math.round(fracToLog(frac) * 100);
  }

  function nudgeSlider(delta) {
    if (!currentDecision) return;
    const { legal } = currentDecision;
    const bb  = currentDecision.snap?.bigBlind || currentBigBlind;
    const cur = parseInt(raiseSlider.value, 10);
    const next = Math.min(100, Math.max(0, cur + delta));
    raiseSlider.value = next;
    const frac    = logToFrac(next / 100);
    const range   = legal.maxRaiseTo - legal.minRaiseTo;
    updateRaiseDisplay(clampRaise(legal.minRaiseTo + frac * range, legal), legal, bb);
  }

  function computeHandStrength(decision) {
    if (!abStrength) return;
    try {
      const heroCards = decision.snap?.heroCards;
      const board     = decision.snap?.board || [];
      if (!heroCards || heroCards.length < 2) { abStrength.textContent = ""; return; }
      const all = [...heroCards, ...board];
      if (all.length < 2) { abStrength.textContent = ""; return; }
      const result = evaluate(all);
      abStrength.textContent = all.length >= 5
        ? result.name
        : (all.length >= 2 ? "Hole: " + result.name : "");
    } catch { abStrength.textContent = ""; }
  }

  function activate(decision, bigBlind) {
    currentDecision = decision;
    currentBigBlind = bigBlind;
    const { legal } = decision;

    setDisabled(false);
    rootEl.classList.add("hero-acting");
    if (abWaiting) abWaiting.textContent = "";

    // Hand-strength label
    computeHandStrength(decision);

    // Facing description
    const streetLabel = (decision.street || "").toUpperCase();
    const potBB = (decision.pot / bigBlind).toFixed(1);
    if (abFacing) {
      if (legal.toCall > 0.001) {
        abFacing.textContent = `${streetLabel} · Pot ${potBB}bb · To call: ${(legal.toCall / bigBlind).toFixed(1)}bb`;
      } else {
        abFacing.textContent = `${streetLabel} · Pot ${potBB}bb · Your action`;
      }
    }

    // Merged Check/Call button
    if (btnCheckCall) {
      if (legal.canCall) {
        checkCallMode = "call";
        btnCheckCall.textContent = `Call ${(legal.callAmount / bigBlind).toFixed(1)}bb`;
        btnCheckCall.classList.add("can-call");
        btnCheckCall.style.display = "";
      } else if (legal.canCheck) {
        checkCallMode = "check";
        btnCheckCall.textContent = "Check";
        btnCheckCall.classList.remove("can-call");
        btnCheckCall.style.display = "";
      } else {
        btnCheckCall.style.display = "none";
      }
    }

    // Raise button
    if (btnRaise) btnRaise.style.display = legal.canRaise ? "" : "none";
    if (abSizing) abSizing.style.display = legal.canRaise ? "" : "none";

    // Raise sizing
    if (legal.canRaise) {
      if (raiseSlider) { raiseSlider.min = 0; raiseSlider.max = 100; }
      if (!legal.canFullRaise) {
        if (abSizing) abSizing.style.display = "none";
        if (raiseAmt) raiseAmt.textContent = (legal.maxRaiseTo / bigBlind).toFixed(1) + "bb";
        currentRaiseTo = legal.maxRaiseTo;
      } else {
        updateRaiseDisplay(legal.minRaiseTo, legal, bigBlind);
      }
    }
  }

  function deactivate() {
    setDisabled(true);
    rootEl.classList.remove("hero-acting");
    if (abFacing)   abFacing.textContent  = "";
    if (abStrength) abStrength.textContent = "";
    if (abWaiting)  abWaiting.textContent = "Waiting…";
    currentDecision = null;
  }

  function calcPreset(preset, legal, bigBlind) {
    const { minRaiseTo, maxRaiseTo, currentBet, pot } = legal;
    if (preset === "allin") return maxRaiseTo;
    if (preset === "min")   return minRaiseTo;
    const frac = parseInt(preset, 10) / 100;
    const candidate = (currentBet || 0) + frac * (pot || bigBlind * 2);
    return clampRaise(candidate, legal);
  }

  // Preset buttons
  for (const btn of presets) {
    btn.addEventListener("click", () => {
      if (!currentDecision) return;
      const { legal } = currentDecision;
      const bb = currentDecision.snap?.bigBlind || currentBigBlind;
      updateRaiseDisplay(calcPreset(btn.dataset.preset, legal, bb), legal, bb);
    });
  }

  // Log-curve slider
  if (raiseSlider) {
    raiseSlider.addEventListener("input", () => {
      if (!currentDecision) return;
      const { legal } = currentDecision;
      const bb   = currentDecision.snap?.bigBlind || currentBigBlind;
      const frac = logToFrac(raiseSlider.value / 100);
      const range = legal.maxRaiseTo - legal.minRaiseTo;
      updateRaiseDisplay(clampRaise(legal.minRaiseTo + frac * range, legal), legal, bb);
    });
  }

  // Manual BB input
  if (raiseInput) {
    raiseInput.addEventListener("change", () => {
      if (!currentDecision) return;
      const { legal } = currentDecision;
      const bb = currentDecision.snap?.bigBlind || currentBigBlind;
      updateRaiseDisplay(clampRaise(parseFloat(raiseInput.value || "0") * bb, legal), legal, bb);
    });
  }

  function submit(actionInput) {
    if (!currentDecision) return;
    document.querySelectorAll(".seat.your-turn").forEach(el => el.classList.remove("your-turn"));
    deactivate();
    bridge.submitAction(actionInput);
  }

  // Action buttons
  if (btnFold) {
    btnFold.addEventListener("click", () => { soundFold(); submit({ action: { type: "fold" } }); });
  }
  if (btnCheckCall) {
    btnCheckCall.addEventListener("click", () => {
      if (checkCallMode === "call") { soundChip(); submit({ action: { type: "call" } }); }
      else                          { soundClick(); submit({ action: { type: "check" } }); }
    });
  }
  if (btnRaise) {
    btnRaise.addEventListener("click", () => {
      if (!currentDecision) return;
      const { legal } = currentDecision;
      soundChip();
      if (!legal.canFullRaise) submit({ action: { type: "allin" } });
      else                      submit({ action: { type: "raise", total: currentRaiseTo } });
    });
  }

  // Keyboard shortcuts (not when typing in an input)
  window.addEventListener("keydown", e => {
    if (!currentDecision) return;
    const tag = document.activeElement?.tagName;
    if (tag === "SELECT") return;
    if (tag === "INPUT" && e.key !== "Enter" && e.key !== "Escape") return;

    switch (e.key.toLowerCase()) {
      case "f":
        e.preventDefault(); btnFold?.click(); break;
      case "c":
        e.preventDefault(); btnCheckCall?.click(); break;
      case "r":
        e.preventDefault(); raiseInput?.focus(); break;
      case "enter":
        if (document.activeElement === raiseInput || document.activeElement === raiseSlider) {
          e.preventDefault(); btnRaise?.click();
        }
        break;
      case "arrowleft":
        if (!raiseInput || document.activeElement !== raiseInput) { e.preventDefault(); nudgeSlider(-5); }
        break;
      case "arrowright":
        if (!raiseInput || document.activeElement !== raiseInput) { e.preventDefault(); nudgeSlider(5); }
        break;
    }
  });

  return { activate, deactivate };
}
