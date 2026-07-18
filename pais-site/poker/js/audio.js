// play/js/audio.js — Synthesized poker sounds via Web Audio API. No files needed.

let ctx    = null;
let muted  = false;

export function setMuted(m) { muted = m; }
export function getMuted()  { return muted; }

function ac() {
  if (muted) return null;
  if (!ctx) ctx = new AudioContext();
  if (ctx.state === "suspended") ctx.resume();
  return ctx;
}

// Rapid flutter of cards being dealt at the start of a hand
export function soundDeal() {
  const c = ac(); if (!c) return;
  for (let k = 0; k < 3; k++) {
    const t = c.currentTime + k * 0.07;
    const len = Math.floor(c.sampleRate * 0.065);
    const buf = c.createBuffer(1, len, c.sampleRate);
    const d = buf.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / len);
    const src = c.createBufferSource();
    src.buffer = buf;
    const bp = c.createBiquadFilter();
    bp.type = "bandpass";
    bp.frequency.value = 4000;
    bp.Q.value = 1.2;
    const g = c.createGain();
    g.gain.setValueAtTime(0.18, t);
    g.gain.exponentialRampToValueAtTime(0.001, t + 0.065);
    src.connect(bp);
    bp.connect(g);
    g.connect(c.destination);
    src.start(t);
  }
}

// Single card flip — hero hole cards revealed
export function soundCardFlip() {
  const c = ac(); if (!c) return;
  const len = Math.floor(c.sampleRate * 0.1);
  const buf = c.createBuffer(1, len, c.sampleRate);
  const d = buf.getChannelData(0);
  for (let i = 0; i < len; i++) {
    d[i] = (Math.random() * 2 - 1) * Math.sin(Math.PI * i / len);
  }
  const src = c.createBufferSource();
  src.buffer = buf;
  const bp = c.createBiquadFilter();
  bp.type = "bandpass";
  bp.frequency.value = 3400;
  bp.Q.value = 0.9;
  const g = c.createGain();
  g.gain.setValueAtTime(0.28, c.currentTime);
  g.gain.exponentialRampToValueAtTime(0.001, c.currentTime + 0.1);
  src.connect(bp);
  bp.connect(g);
  g.connect(c.destination);
  src.start();
}

// Casino chip click — bet, call, raise, blind
export function soundChip() {
  const c = ac(); if (!c) return;
  const osc = c.createOscillator();
  osc.type = "triangle";
  osc.frequency.setValueAtTime(1500, c.currentTime);
  osc.frequency.exponentialRampToValueAtTime(450, c.currentTime + 0.05);
  const g = c.createGain();
  g.gain.setValueAtTime(0.2, c.currentTime);
  g.gain.exponentialRampToValueAtTime(0.001, c.currentTime + 0.09);
  osc.connect(g);
  g.connect(c.destination);
  osc.start();
  osc.stop(c.currentTime + 0.09);
}

// Soft thud — fold (cards hit the muck)
export function soundFold() {
  const c = ac(); if (!c) return;
  const osc = c.createOscillator();
  osc.type = "sine";
  osc.frequency.setValueAtTime(210, c.currentTime);
  osc.frequency.exponentialRampToValueAtTime(65, c.currentTime + 0.14);
  const g = c.createGain();
  g.gain.setValueAtTime(0.18, c.currentTime);
  g.gain.exponentialRampToValueAtTime(0.001, c.currentTime + 0.16);
  osc.connect(g);
  g.connect(c.destination);
  osc.start();
  osc.stop(c.currentTime + 0.16);
}

// Ascending C-E-G chime — hero wins the pot
export function soundWin() {
  const c = ac(); if (!c) return;
  [523.25, 659.25, 783.99].forEach((freq, i) => {
    const t = c.currentTime + i * 0.15;
    const osc = c.createOscillator();
    osc.type = "sine";
    osc.frequency.setValueAtTime(freq, t);
    const g = c.createGain();
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(0.22, t + 0.02);
    g.gain.exponentialRampToValueAtTime(0.001, t + 0.46);
    osc.connect(g);
    g.connect(c.destination);
    osc.start(t);
    osc.stop(t + 0.46);
  });
}

// Subtle tick — check button / confirm
export function soundClick() {
  const c = ac(); if (!c) return;
  const osc = c.createOscillator();
  osc.type = "square";
  osc.frequency.setValueAtTime(900, c.currentTime);
  const g = c.createGain();
  g.gain.setValueAtTime(0.06, c.currentTime);
  g.gain.exponentialRampToValueAtTime(0.001, c.currentTime + 0.025);
  osc.connect(g);
  g.connect(c.destination);
  osc.start();
  osc.stop(c.currentTime + 0.025);
}

// Soft rising whoosh — drawer opens
export function soundDrawer() {
  const c = ac(); if (!c) return;
  const dur = 0.16;
  const len = Math.floor(c.sampleRate * dur);
  const buf = c.createBuffer(1, len, c.sampleRate);
  const d = buf.getChannelData(0);
  for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
  const src = c.createBufferSource();
  src.buffer = buf;
  const hp = c.createBiquadFilter();
  hp.type = "highpass";
  hp.frequency.setValueAtTime(1800, c.currentTime);
  hp.frequency.exponentialRampToValueAtTime(7000, c.currentTime + dur);
  const g = c.createGain();
  g.gain.setValueAtTime(0.09, c.currentTime);
  g.gain.exponentialRampToValueAtTime(0.001, c.currentTime + dur);
  src.connect(hp);
  hp.connect(g);
  g.connect(c.destination);
  src.start();
}

// Soft slide of chips moving across felt — bets to pot, pot to winner
export function soundChipSlide() {
  const c = ac(); if (!c) return;
  const dur = 0.22;
  const len = Math.floor(c.sampleRate * dur);
  const buf = c.createBuffer(1, len, c.sampleRate);
  const d = buf.getChannelData(0);
  for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * 0.5;
  const src = c.createBufferSource();
  src.buffer = buf;
  const lp = c.createBiquadFilter();
  lp.type = "lowpass";
  lp.frequency.setValueAtTime(600, c.currentTime);
  lp.frequency.exponentialRampToValueAtTime(200, c.currentTime + dur);
  const g = c.createGain();
  g.gain.setValueAtTime(0.12, c.currentTime);
  g.gain.exponentialRampToValueAtTime(0.001, c.currentTime + dur);
  src.connect(lp);
  lp.connect(g);
  g.connect(c.destination);
  src.start();
}
