// PAIS // Batcave on getpais.company — authenticator gate, five tabs, audio.
import { api, h, BAT_PATH, ago } from "./ui.js";
import { sfx, startAmbient, stopAmbient, playAura, stopAura, playScore, stopScore, auraAvailable, unlockAudio } from "./audio.js";
import { say, greeting, setVoice } from "./alfred.js";
import agents from "./tabs/agents.js";
import trading from "./tabs/trading.js";
import job from "./tabs/job.js";
import gym from "./tabs/gym.js";
import academics from "./tabs/academics.js";

const TABS = [agents, trading, job, gym, academics];
const CODE_LEN = 6;
const REFRESH_MS = 5 * 60 * 1000;
const PREFS_KEY = "pais-prefs";
const $ = id => document.getElementById(id);

const prefs = (() => {
  const base = { cave: true, aura: true, voice: true };
  try { return { ...base, ...JSON.parse(localStorage.getItem(PREFS_KEY) || "{}") }; } catch { return base; }
})();
const savePrefs = () => { try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch { /* private mode */ } };

document.querySelectorAll("svg[data-bat]").forEach(svg => {
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
  p.setAttribute("d", BAT_PATH);
  svg.append(p);
});

// ---------------- gate ----------------
function buildGate() {
  const cells = $("cells");
  const inputs = Array.from({ length: CODE_LEN }, (_, i) =>
    h("input", { maxlength: "1", autocapitalize: "characters", spellcheck: "false", "aria-label": `Character ${i + 1}`, autocomplete: i === 0 ? "one-time-code" : "off" }));
  const clean = text => (text || "").replace(/[^0-9a-z]/gi, "").toUpperCase();
  cells.append(...inputs);
  let armed = false;
  const code = () => inputs.map(i => i.value).join("");
  const arm = () => {
    if (armed) return;
    armed = true;
    unlockAudio();
    $("gate").classList.add("lit");
    sfx.whoosh();
    if (prefs.cave) startAmbient();
  };
  const fillFrom = (start, digits) => {
    digits.split("").slice(0, CODE_LEN - start).forEach((d, k) => { inputs[start + k].value = d; });
    inputs[Math.min(start + digits.length, CODE_LEN - 1)].focus();
    if (code().length === CODE_LEN) submit(code());
  };
  inputs.forEach((input, i) => {
    input.addEventListener("focus", arm);
    input.addEventListener("input", () => {
      const digits = clean(input.value);
      input.value = "";
      if (digits) { sfx.tick(); fillFrom(i, digits); }
    });
    input.addEventListener("keydown", e => {
      if (e.key === "Backspace" && !input.value && i > 0) { inputs[i - 1].value = ""; inputs[i - 1].focus(); }
      if (e.key === "ArrowLeft" && i > 0) inputs[i - 1].focus();
      if (e.key === "ArrowRight" && i < CODE_LEN - 1) inputs[i + 1].focus();
    });
    input.addEventListener("paste", e => {
      e.preventDefault();
      fillFrom(0, clean(e.clipboardData.getData("text")));
    });
  });
  $("gate-form").addEventListener("submit", e => { e.preventDefault(); if (code().length === CODE_LEN) submit(code()); });

  async function submit(value) {
    inputs.forEach(i => { i.disabled = true; });
    $("gate-prompt").textContent = "Verifying…";
    try {
      await api("/login", { method: "POST", body: JSON.stringify({ code: value }), lockOn401: false });
      cells.classList.add("good");
      enterCave(true);
    } catch (err) {
      sfx.deny();
      cells.classList.remove("bad");
      void cells.offsetWidth;  // restart the shake animation
      cells.classList.add("bad");
      $("gate-prompt").textContent = err.message.includes("Lockdown") ? err.message : "Access denied. The code has changed, perhaps?";
      inputs.forEach(i => { i.disabled = false; i.value = ""; });
      inputs[0].focus();
    }
  }
  inputs[0].focus();
}

// ---------------- cave ----------------
let current = null;
let snapshot = null;
let aura = { play() {}, stop() {} };

function staleBanner() {
  const slot = $("stale");
  if (!snapshot?.stale) { slot.hidden = true; return; }
  slot.hidden = false;
  slot.textContent = `The Mac last reported ${ago(snapshot.generated_at)}. Figures may be out of date until it's back online.`;
}

function show(tab, { announce = true } = {}) {
  if (current && current !== tab) current.leave?.();  // e.g. stop live price polling
  current = tab;
  document.querySelectorAll(".tab").forEach(b => b.setAttribute("aria-selected", String(b.dataset.id === tab.id)));
  history.replaceState(null, "", `#${tab.id}`);
  const panel = h("div", { class: "panel" });
  let line;
  try {
    line = tab.render(panel, snapshot, { speak: announce });
  } catch (err) {
    panel.replaceChildren(h("section", { class: "card alert" }, h("p", { class: "err" }, `Signal lost: ${err.message}`)));
    line = "I'm afraid that system isn't answering, sir.";
  }
  $("view").replaceChildren(panel);
  if (announce && line) say(line);
}

async function load() {
  snapshot = await api("/snapshot");
  staleBanner();
}

function buildTabs() {
  $("tabs").append(...TABS.map((t, i) => h("button", {
    class: "tab", role: "tab", type: "button", "data-id": t.id, "aria-selected": "false",
    onclick: () => { if (current !== t) { sfx.ping(); show(t); } },
  }, h("span", {}, `0${i + 1}`), t.label)));
  addEventListener("keydown", e => {
    if (e.target.matches("input, select, textarea") || e.metaKey || e.ctrlKey || e.altKey) return;
    const n = Number(e.key);
    if (n >= 1 && n <= TABS.length && current !== TABS[n - 1]) { sfx.ping(); show(TABS[n - 1]); }
  });
}

async function buildToggles() {
  const bind = (id, key, on, off) => {
    const btn = $(id);
    btn.setAttribute("aria-pressed", String(prefs[key]));
    btn.addEventListener("click", () => {
      prefs[key] = !prefs[key];
      btn.setAttribute("aria-pressed", String(prefs[key]));
      savePrefs();
      sfx.tick();
      (prefs[key] ? on : off)();
    });
  };
  bind("t-cave", "cave", startAmbient, stopAmbient);
  bind("t-voice", "voice", () => setVoice(true), () => setVoice(false));
  setVoice(prefs.voice);
  const hasTrack = await auraAvailable();
  aura = hasTrack ? { play: playAura, stop: stopAura } : { play: playScore, stop: stopScore };
  $("t-aura").title = hasTrack ? "Aura track" : "Synthesized aura score";
  bind("t-aura", "aura", () => aura.play(), () => aura.stop());
  $("t-lock").addEventListener("click", lock);
}

async function enterCave(fromLogin) {
  const gate = $("gate");
  if (fromLogin) { sfx.unlock(); gate.classList.add("opening"); } else { gate.hidden = true; }
  document.body.classList.add("in-cave");  // switches to the lit Batmobile bunker theme
  const cave = $("cave");
  cave.hidden = false;
  requestAnimationFrame(() => cave.classList.add("on"));
  setTimeout(() => { gate.hidden = true; }, 900);
  await buildToggles();
  if (fromLogin && prefs.aura) aura.play();
  buildTabs();
  const tick = () => { $("clock").textContent = new Date().toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" }); };
  setInterval(tick, 1000);
  tick();
  $("view").replaceChildren(h("p", { class: "empty" }, "Accessing the Batcomputer…"));
  try {
    await load();
  } catch (err) {
    if (err.message === "locked") return;
    $("view").replaceChildren(h("section", { class: "card alert" }, h("p", { class: "err" }, `Signal lost: ${err.message}`)));
    say("The Batcomputer hasn't reported in, sir. Your Mac may be asleep.");
    return;
  }
  setInterval(async () => {
    try { await load(); if (current) show(current, { announce: false }); } catch { /* keep last good data */ }
  }, REFRESH_MS);
  if (fromLogin) say(greeting());
  show(TABS.find(t => `#${t.id}` === location.hash) || agents, { announce: !fromLogin });
}

async function lock() {
  await fetch("/api/cave/logout", { method: "POST" }).catch(() => {});
  location.hash = "";
  location.reload();
}

document.addEventListener("pais:locked", () => location.reload());

// ---------------- boot ----------------
api("/session").then(s => {
  if (!s.authenticated) { buildGate(); return; }
  enterCave(false);
  addEventListener("pointerdown", () => {
    unlockAudio();
    if (prefs.cave) startAmbient();
    if (prefs.aura) aura.play();
  }, { once: true });
}).catch(() => buildGate());
