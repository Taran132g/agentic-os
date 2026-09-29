// Alfred: the butler console. Types each line on screen and speaks it with a British voice.
import { sfx } from "./audio.js";

const TYPE_MS = 18;
const PREFERRED_VOICES = ["Daniel", "Google UK English Male", "Arthur", "Oliver"];

let voiceOn = true;
let voice = null;
let typingTimer = null;

function pickVoice() {
  const voices = speechSynthesis.getVoices();
  voice = PREFERRED_VOICES.map(n => voices.find(v => v.name.startsWith(n))).find(Boolean)
    || voices.find(v => v.lang === "en-GB") || null;
}
if ("speechSynthesis" in window) {
  pickVoice();
  speechSynthesis.addEventListener("voiceschanged", pickVoice);
}

export function setVoice(on) {
  voiceOn = on;
  if (!on && "speechSynthesis" in window) speechSynthesis.cancel();
}

function speak(text) {
  if (!voiceOn || !("speechSynthesis" in window)) return;
  const box = document.getElementById("alfred");
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  if (voice) u.voice = voice;
  u.lang = "en-GB";
  u.rate = 0.96;
  u.pitch = 0.88;
  u.onstart = () => box.classList.add("speaking");
  u.onend = u.onerror = () => box.classList.remove("speaking");
  speechSynthesis.speak(u);
}

export function say(text, { silent = false } = {}) {
  const box = document.getElementById("alfred");
  const out = document.getElementById("alfred-said");
  box.hidden = false;
  box.classList.remove("idle");
  clearInterval(typingTimer);
  out.textContent = "";
  let i = 0;
  typingTimer = setInterval(() => {
    out.textContent = text.slice(0, ++i);
    if (i % 3 === 0 && !voiceOn) sfx.tick();
    if (i >= text.length) {
      clearInterval(typingTimer);
      box.classList.add("idle");
    }
  }, TYPE_MS);
  if (!silent) speak(text);
}

export function greeting(now = new Date()) {
  const h = now.getHours();
  if (h < 5) return "Burning the midnight oil again, Master Taran.";
  if (h < 12) return "Good morning, Master Taran. The Batcomputer is at your disposal.";
  if (h < 18) return "Good afternoon, Master Taran. Everything is where you left it.";
  return "Good evening, Master Taran. Gotham awaits.";
}
