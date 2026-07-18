// play/js/render/gameLog.js — Live action log strip above the action bar.

const SUIT_SYM = { s: "♠", h: "♥", d: "♦", c: "♣" };

export function createGameLog(wrapEl) {
  if (!wrapEl) return null;

  const logEl = document.createElement("div");
  logEl.className = "game-log";
  logEl.setAttribute("aria-live", "polite");
  logEl.setAttribute("aria-label", "Game log");
  wrapEl.appendChild(logEl);

  const lines = [];
  let expanded = false;

  logEl.addEventListener("click", () => {
    expanded = !expanded;
    logEl.classList.toggle("game-log-expanded", expanded);
  });

  function addLine(text, cls) {
    const line = document.createElement("div");
    line.className = cls ? `game-log-line ${cls}` : "game-log-line";
    line.textContent = text;
    logEl.appendChild(line);
    lines.push(line);
    if (lines.length > 60) { lines[0].remove(); lines.shift(); }
    logEl.scrollTop = logEl.scrollHeight;
  }

  function cardStr(c) {
    if (!c) return "";
    const rank = c[0] === "T" ? "10" : c[0];
    return rank + (SUIT_SYM[c[1]] || c[1]);
  }

  function onEvent(evt, state) {
    const bb = state?.bigBlind || 1;
    switch (evt.kind) {
      case "hand_start":
        addLine("── New Hand ──", "log-hand");
        break;

      case "street":
        if (evt.name !== "preflop") {
          const boardStr = (evt.board || []).map(cardStr).join(" ");
          addLine(`${evt.name.toUpperCase()}${boardStr ? " · " + boardStr : ""}`, "log-street");
        }
        break;

      case "action": {
        const player = state?.seated?.[evt.seatIdx];
        if (!player) break;
        const name = player.id === "human" ? "You" : player.name;
        const a = evt.action;
        let label;
        if (a.type === "fold")  label = "fold";
        else if (a.type === "check") label = "check";
        else if (a.type === "call")  label = `call ${(a.amount / bb).toFixed(1)}bb`;
        else if (a.type === "blind") label = `blind ${(a.amount / bb).toFixed(1)}bb`;
        else if (a.type === "raise") label = `raise to ${(a.total / bb).toFixed(1)}bb`;
        else if (a.type === "allin") label = `all-in (${((a.total ?? a.amount ?? 0) / bb).toFixed(1)}bb)`;
        else label = a.type;
        const cls = player.id === "human" ? "log-hero" : "";
        addLine(`${name} ${label}`, cls);
        break;
      }

      case "showdown":
        if (evt.pots && state?.seated) {
          for (const pot of evt.pots) {
            const names = pot.winners.map(wi => {
              const p = state.seated[wi];
              return p?.id === "human" ? "You" : (p?.name || "?");
            }).join(" & ");
            addLine(`${names} win${pot.winners.length === 1 ? "s" : ""} ${(pot.amount / bb).toFixed(1)}bb`, "log-win");
          }
        }
        break;
    }
  }

  function clear() {
    lines.length = 0;
    logEl.innerHTML = "";
    expanded = false;
    logEl.classList.remove("game-log-expanded");
  }

  return { onEvent, clear };
}
