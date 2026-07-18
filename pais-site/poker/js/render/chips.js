// play/js/render/chips.js — CSS chip disc stacks for bet display.

const DENOM = [
  { min: 150, cls: "chip-500", color: "var(--chip-500)" },
  { min:  50, cls: "chip-100", color: "var(--chip-100)" },
  { min:  12, cls: "chip-25",  color: "var(--chip-25)"  },
  { min:   3, cls: "chip-5",   color: "var(--chip-5)"   },
  { min:   0, cls: "chip-1",   color: "var(--chip-1)"   },
];

function chipDenom(bbs) {
  for (const d of DENOM) {
    if (bbs >= d.min) return d;
  }
  return DENOM[DENOM.length - 1];
}

export function createBetEl(amountBB) {
  const el = document.createElement("div");
  el.className = "chip-stack";
  if (!amountBB || amountBB < 0.01) return el;

  const { cls, color } = chipDenom(amountBB);
  const layers = Math.min(Math.ceil(Math.log2(amountBB / 2 + 1)), 5);

  // box-shadows simulate stacked chip layers (each layer shifts up 4px)
  const shadows = [];
  for (let i = 1; i < layers; i++) shadows.push(`0 ${-i * 4}px 0 ${color}`);
  shadows.push("0 2px 4px rgba(0,0,0,0.5)");

  const disc = document.createElement("div");
  disc.className  = `chip-disc ${cls}`;
  disc.style.boxShadow = shadows.join(",");

  const label = document.createElement("span");
  label.className   = "chip-label";
  label.textContent = amountBB.toFixed(1);

  el.appendChild(disc);
  el.appendChild(label);
  return el;
}
