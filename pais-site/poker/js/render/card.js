// play/js/render/card.js — Proper card component with corner rank+suit indices.

const SUIT_SYM = { s: "♠", h: "♥", d: "♦", c: "♣" };
const SUIT_COLOR_4 = { s: "card-suit-black", h: "card-suit-red", d: "card-suit-blue", c: "card-suit-green" };

export function createCard(cardStr, { animate = false, back = false, fourColor = false } = {}) {
  const el = document.createElement("div");

  if (back || !cardStr || cardStr === "?") {
    el.className = "card card-back" + (animate ? " dealing" : "");
    el.innerHTML = '<div class="card-back-inner"></div>';
    return el;
  }

  const rank = cardStr[0] === "T" ? "10" : cardStr[0];
  const suit = cardStr[1];
  const sym  = SUIT_SYM[suit] || suit;
  const isRed = !fourColor && (suit === "h" || suit === "d");
  const suitCls = fourColor
    ? (SUIT_COLOR_4[suit] || "")
    : (isRed ? "card-red-suit" : "");

  el.className = ["card", suitCls, animate ? "dealing" : ""].filter(Boolean).join(" ");
  el.dataset.card = cardStr;
  el.innerHTML = `
    <div class="card-corner card-corner-tl"><span class="card-rank">${rank}</span><span class="card-csuit">${sym}</span></div>
    <span class="card-center" aria-hidden="true">${sym}</span>
    <div class="card-corner card-corner-br" aria-hidden="true"><span class="card-rank">${rank}</span><span class="card-csuit">${sym}</span></div>`;
  return el;
}

export function createCardBack(animate = false) {
  return createCard("?", { back: true, animate });
}
