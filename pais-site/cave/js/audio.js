// Batcave sound engine: everything synthesized with Web Audio (no files needed),
// plus an optional user-supplied aura track at /cave/audio/aura.mp3.

const AMBIENT_LEVEL = 0.05;
const SFX_LEVEL = 0.22;
const AURA_LEVEL = 0.32;
const DRIP_MIN_MS = 2200;
const DRIP_MAX_MS = 7000;

let ctx = null;
let master = null;
let ambient = null;
let dripTimer = null;

function ensure() {
  if (!ctx) {
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    master = ctx.createGain();
    master.gain.value = 1;
    master.connect(ctx.destination);
  }
  if (ctx.state === "suspended") ctx.resume();
  return ctx;
}

function noiseBuffer(seconds = 2) {
  const buf = ctx.createBuffer(1, ctx.sampleRate * seconds, ctx.sampleRate);
  const data = buf.getChannelData(0);
  for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
  return buf;
}

function env(node, peak, attack, release, t = ctx.currentTime) {
  node.gain.setValueAtTime(0.0001, t);
  node.gain.exponentialRampToValueAtTime(peak, t + attack);
  node.gain.exponentialRampToValueAtTime(0.0001, t + attack + release);
}

// ---- one-shots -----------------------------------------------------------
export const sfx = {
  // Batcomputer sonar: a clean sine with a decaying echo.
  ping(freq = 1320) {
    ensure();
    const t = ctx.currentTime;
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    const delay = ctx.createDelay();
    const fb = ctx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(freq, t);
    osc.frequency.exponentialRampToValueAtTime(freq * 0.92, t + 0.4);
    delay.delayTime.value = 0.16;
    fb.gain.value = 0.32;
    env(g, SFX_LEVEL * 0.5, 0.005, 0.5, t);
    osc.connect(g).connect(master);
    g.connect(delay).connect(fb).connect(delay);
    delay.connect(master);
    osc.start(t);
    osc.stop(t + 1.2);
  },
  // Soft key tick for code entry and Alfred's typing.
  tick() {
    ensure();
    const t = ctx.currentTime;
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.type = "square";
    osc.frequency.value = 2400 + Math.random() * 400;
    env(g, SFX_LEVEL * 0.08, 0.001, 0.03, t);
    osc.connect(g).connect(master);
    osc.start(t);
    osc.stop(t + 0.05);
  },
  // Bat wings: band-passed noise sweep.
  whoosh() {
    ensure();
    const t = ctx.currentTime;
    const src = ctx.createBufferSource();
    const bp = ctx.createBiquadFilter();
    const g = ctx.createGain();
    src.buffer = noiseBuffer(0.6);
    bp.type = "bandpass";
    bp.Q.value = 1.2;
    bp.frequency.setValueAtTime(300, t);
    bp.frequency.exponentialRampToValueAtTime(2600, t + 0.28);
    env(g, SFX_LEVEL * 0.6, 0.06, 0.3, t);
    src.connect(bp).connect(g).connect(master);
    src.start(t);
  },
  // Vault door: sub drop + metallic clank + air release.
  unlock() {
    ensure();
    const t = ctx.currentTime;
    const sub = ctx.createOscillator();
    const sg = ctx.createGain();
    sub.type = "sine";
    sub.frequency.setValueAtTime(110, t);
    sub.frequency.exponentialRampToValueAtTime(32, t + 1.1);
    env(sg, SFX_LEVEL * 1.6, 0.01, 1.2, t);
    sub.connect(sg).connect(master);
    sub.start(t);
    sub.stop(t + 1.4);
    [220, 331, 467].forEach((f, i) => {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = "triangle";
      o.frequency.value = f;
      env(g, SFX_LEVEL * 0.18, 0.002, 0.5 + i * 0.1, t + 0.02);
      o.connect(g).connect(master);
      o.start(t);
      o.stop(t + 1);
    });
    setTimeout(() => sfx.whoosh(), 380);
  },
  // Access denied: two low buzzes.
  deny() {
    ensure();
    [0, 0.18].forEach(offset => {
      const t = ctx.currentTime + offset;
      const o = ctx.createOscillator();
      const lp = ctx.createBiquadFilter();
      const g = ctx.createGain();
      o.type = "sawtooth";
      o.frequency.value = 96;
      lp.type = "lowpass";
      lp.frequency.value = 700;
      env(g, SFX_LEVEL * 0.7, 0.005, 0.14, t);
      o.connect(lp).connect(g).connect(master);
      o.start(t);
      o.stop(t + 0.2);
    });
  },
};

// ---- cave ambience ---------------------------------------------------------
function drip() {
  if (!ambient) return;
  const t = ctx.currentTime;
  const o = ctx.createOscillator();
  const g = ctx.createGain();
  const f = 900 + Math.random() * 900;
  o.type = "sine";
  o.frequency.setValueAtTime(f, t);
  o.frequency.exponentialRampToValueAtTime(f * 1.6, t + 0.05);
  env(g, 0.035, 0.002, 0.25, t);
  o.connect(g).connect(ambient.bus);
  o.start(t);
  o.stop(t + 0.3);
  dripTimer = setTimeout(drip, DRIP_MIN_MS + Math.random() * (DRIP_MAX_MS - DRIP_MIN_MS));
}

export function startAmbient() {
  ensure();
  if (ambient) return;
  const bus = ctx.createGain();
  bus.gain.setValueAtTime(0.0001, ctx.currentTime);
  bus.gain.exponentialRampToValueAtTime(AMBIENT_LEVEL, ctx.currentTime + 3);
  bus.connect(master);
  const lp = ctx.createBiquadFilter();
  lp.type = "lowpass";
  lp.frequency.value = 180;
  lp.connect(bus);
  const drones = [49, 49.35, 73.5].map(f => {
    const o = ctx.createOscillator();
    o.type = "sawtooth";
    o.frequency.value = f;
    o.connect(lp);
    o.start();
    return o;
  });
  const wind = ctx.createBufferSource();
  const wbp = ctx.createBiquadFilter();
  const wg = ctx.createGain();
  wind.buffer = noiseBuffer(4);
  wind.loop = true;
  wbp.type = "bandpass";
  wbp.frequency.value = 420;
  wbp.Q.value = 0.6;
  wg.gain.value = 0.35;
  const lfo = ctx.createOscillator();
  const lfoGain = ctx.createGain();
  lfo.frequency.value = 0.07;
  lfoGain.gain.value = 180;
  lfo.connect(lfoGain).connect(wbp.frequency);
  wind.connect(wbp).connect(wg).connect(bus);
  wind.start();
  lfo.start();
  ambient = { bus, nodes: [...drones, wind, lfo] };
  dripTimer = setTimeout(drip, 1500);
}

export function stopAmbient() {
  if (!ambient) return;
  const { bus, nodes } = ambient;
  ambient = null;
  clearTimeout(dripTimer);
  bus.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 1.2);
  setTimeout(() => nodes.forEach(n => n.stop()), 1300);
}

// ---- aura track (user-supplied mp3) ----------------------------------------
const auraEl = () => document.getElementById("aura");

export async function auraAvailable() {
  try {
    const res = await fetch("/cave/audio/aura.mp3", { method: "HEAD" });
    return res.ok;
  } catch {
    return false;
  }
}

export function playAura() {
  const el = auraEl();
  el.volume = 0;
  el.play().catch(() => {});
  const start = performance.now();
  const fade = now => {
    const k = Math.min(1, (now - start) / 4000);
    el.volume = AURA_LEVEL * k;
    if (k < 1 && !el.paused) requestAnimationFrame(fade);
  };
  requestAnimationFrame(fade);
}

export function stopAura() {
  const el = auraEl();
  const start = performance.now();
  const from = el.volume;
  const fade = now => {
    const k = Math.min(1, (now - start) / 1200);
    el.volume = from * (1 - k);
    if (k < 1) requestAnimationFrame(fade);
    else el.pause();
  };
  requestAnimationFrame(fade);
}

// ---- synthesized aura score (used when no aura.mp3 is supplied) -------------
// A slow C-minor brood: sub pulse, dark pad, and an ominous rising half-step motif.
const SCORE_BPM = 58;
const SCORE_LEVEL = 0.09;
let score = null;

function voice(freq, start, dur, { type = "sawtooth", level = 0.2, cutoff = 900, attack = 0.08 } = {}) {
  const o = ctx.createOscillator();
  const lp = ctx.createBiquadFilter();
  const g = ctx.createGain();
  o.type = type;
  o.frequency.value = freq;
  lp.type = "lowpass";
  lp.frequency.value = cutoff;
  g.gain.setValueAtTime(0.0001, start);
  g.gain.exponentialRampToValueAtTime(level, start + attack);
  g.gain.setValueAtTime(level, start + dur * 0.7);
  g.gain.exponentialRampToValueAtTime(0.0001, start + dur);
  o.connect(lp).connect(g).connect(score.bus);
  o.start(start);
  o.stop(start + dur + 0.05);
}

function scheduleBar(barStart, bar) {
  const beat = 60 / SCORE_BPM;
  for (let b = 0; b < 4; b++) voice(65.41, barStart + b * beat, beat * 0.9, { type: "sine", level: 0.5, cutoff: 200, attack: 0.02 });
  [130.81, 155.56, 196.0].forEach(f => voice(f, barStart, beat * 4, { level: 0.07, cutoff: 500 + (bar % 4) * 150, attack: 1.2 }));
  if (bar % 4 === 3) {
    voice(196.0, barStart + beat * 2, beat, { level: 0.16, cutoff: 1400 });
    voice(207.65, barStart + beat * 3, beat * 1.8, { level: 0.18, cutoff: 1600 });
  }
}

export function playScore() {
  ensure();
  if (score) return;
  const bus = ctx.createGain();
  bus.gain.setValueAtTime(0.0001, ctx.currentTime);
  bus.gain.exponentialRampToValueAtTime(SCORE_LEVEL, ctx.currentTime + 4);
  bus.connect(master);
  const barLen = (60 / SCORE_BPM) * 4;
  score = { bus, bar: 0, next: ctx.currentTime + 0.1 };
  const pump = () => {
    if (!score) return;
    while (score.next < ctx.currentTime + barLen * 2) {
      scheduleBar(score.next, score.bar++);
      score.next += barLen;
    }
    score.timer = setTimeout(pump, 1000);
  };
  pump();
}

export function stopScore() {
  if (!score) return;
  const { bus, timer } = score;
  score = null;
  clearTimeout(timer);
  bus.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 1.5);
  setTimeout(() => bus.disconnect(), 1600);
}

export const unlockAudio = ensure;
