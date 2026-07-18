// play/js/render/table.js — DOM management for the felt table: seats, board,
// pot display, dealer button, chip animations, and turn timer ring.

import { createCard, createCardBack } from "./card.js";
import { createBetEl } from "./chips.js";

// Distinct hues (HSL) for non-hero seats; auto-assigned by display slot
const SEAT_HUES = [220, 155, 38, 290, 178, 55];

export function createTableView(tableEl) {
  let seatEls    = [];
  let dealerEl   = null;
  let lastN      = 4;
  let lastHuman  = 0;
  let lastDealer = null;

  // Reposition seats when viewport resizes
  const resizeObserver = new ResizeObserver(() => positionSeats(lastN, lastHuman));
  resizeObserver.observe(tableEl);

  function initials(name) {
    if (!name) return "?";
    const parts = name.trim().split(/\s+/);
    return parts.length >= 2
      ? (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
      : name.slice(0, 2).toUpperCase();
  }

  function init(agentCount) {
    tableEl.querySelectorAll(".seat, .dealer-btn").forEach(el => el.remove());
    seatEls = [];

    for (let i = 0; i < agentCount; i++) {
      const seat = document.createElement("div");
      seat.className = "seat";
      seat.dataset.seat = i;
      // Assign avatar hue — will be overridden for hero seat via class
      const hue = SEAT_HUES[i % SEAT_HUES.length];
      seat.style.setProperty("--avatar-bg", `hsl(${hue}, 42%, 28%)`);

      seat.innerHTML = `
        <div class="seat-timer">
          <svg class="timer-ring" viewBox="0 0 44 44" aria-hidden="true">
            <circle class="timer-bg" cx="22" cy="22" r="21"/>
            <circle class="timer-arc" cx="22" cy="22" r="21"
              stroke-dasharray="131.9" stroke-dashoffset="131.9"
              style="transform:rotate(-90deg);transform-origin:center"/>
          </svg>
          <div class="seat-avatar">
            <span class="avatar-initials">?</span>
          </div>
        </div>
        <div class="seat-plate">
          <span class="seat-name"></span>
          <span class="seat-pos"></span>
          <div class="seat-stack"></div>
        </div>
        <div class="seat-cards"></div>
        <div class="seat-bet"></div>
        <div class="seat-badge"></div>`;
      tableEl.appendChild(seat);
      seatEls.push(seat);
    }

    dealerEl = document.createElement("div");
    dealerEl.className = "dealer-btn";
    dealerEl.textContent = "D";
    tableEl.appendChild(dealerEl);
  }

  function positionSeats(n, humanSeatIdx) {
    lastN     = n;
    lastHuman = humanSeatIdx;

    const W  = tableEl.offsetWidth  || 600;
    const H  = tableEl.offsetHeight || 380;
    const cx = W / 2;
    const cy = H / 2;
    const rx = cx * 0.80;
    const ry = cy * 0.74;
    const humanAngle = Math.PI / 2; // south

    for (let i = 0; i < n; i++) {
      const displayIdx = (i - humanSeatIdx + n) % n;
      const angle = humanAngle + (displayIdx / n) * 2 * Math.PI;
      const x = cx + rx * Math.cos(angle);
      const y = cy + ry * Math.sin(angle);
      if (seatEls[i]) {
        seatEls[i].style.left = x + "px";
        seatEls[i].style.top  = y + "px";
        seatEls[i].classList.toggle("hero", i === humanSeatIdx);
      }
    }
    // Keep the dealer button glued to its seat when the layout shifts
    if (lastDealer != null) setDealerBtn(lastDealer);
  }

  // Build a bet chip element and put it in the seat's .seat-bet div
  function renderBet(seatEl, amountBB) {
    const betEl = seatEl?.querySelector(".seat-bet");
    if (!betEl) return;
    betEl.innerHTML = "";
    if (amountBB > 0.001) betEl.appendChild(createBetEl(amountBB));
  }

  // Animate all visible bet stacks sliding toward the pot, then clear them
  function animateBetsToPot(animateFn) {
    const potEl = document.getElementById("pot");
    if (!potEl) { clearBets(); return; }
    const potRect = potEl.getBoundingClientRect();

    for (const seatEl of seatEls) {
      const betEl = seatEl?.querySelector(".seat-bet");
      if (!betEl || !betEl.firstChild) continue;

      const betRect = betEl.getBoundingClientRect();
      const clone = betEl.firstChild.cloneNode(true);
      clone.style.cssText = `
        position:fixed;left:${betRect.left}px;top:${betRect.top}px;
        z-index:50;pointer-events:none;transition:left 280ms ease,top 280ms ease,opacity 200ms 120ms ease,transform 280ms ease;`;
      document.body.appendChild(clone);

      requestAnimationFrame(() => requestAnimationFrame(() => {
        clone.style.left      = (potRect.left + potRect.width  / 2 - 11) + "px";
        clone.style.top       = (potRect.top  + potRect.height / 2 - 5)  + "px";
        clone.style.transform = "scale(0.25)";
        clone.style.opacity   = "0";
        setTimeout(() => clone.remove(), 380);
      }));

      betEl.innerHTML = "";
    }
    if (animateFn) animateFn();
  }

  function clearBets() {
    for (const seatEl of seatEls) {
      const betEl = seatEl?.querySelector(".seat-bet");
      if (betEl) betEl.innerHTML = "";
    }
  }

  // Animate a win chip sliding from pot to winner seat
  function animatePotToWinner(winnerSeatIdx, potBB) {
    const potEl  = document.getElementById("pot");
    const seatEl = seatEls[winnerSeatIdx];
    if (!potEl || !seatEl) return;

    const potRect  = potEl.getBoundingClientRect();
    const seatRect = seatEl.getBoundingClientRect();

    const chip = document.createElement("div");
    chip.className = "chip-disc chip-100";
    chip.style.cssText = `
      position:fixed;
      left:${potRect.left + potRect.width / 2 - 11}px;
      top:${potRect.top  + potRect.height / 2 - 5}px;
      z-index:50;pointer-events:none;width:22px;height:9px;border-radius:50%;
      transition:left 380ms ease,top 380ms ease,opacity 200ms 260ms ease;`;
    document.body.appendChild(chip);

    requestAnimationFrame(() => requestAnimationFrame(() => {
      chip.style.left    = (seatRect.left + seatRect.width  / 2 - 11) + "px";
      chip.style.top     = (seatRect.top  + seatRect.height / 2 - 5)  + "px";
      chip.style.opacity = "0";
      setTimeout(() => chip.remove(), 600);
    }));
  }

  // Floating +/- profit text over a seat
  function showFloatingProfit(seatIdx, profitBB) {
    const seatEl = seatEls[seatIdx];
    if (!seatEl) return;
    const rect = seatEl.getBoundingClientRect();
    const el   = document.createElement("div");
    el.className = "profit-float";
    el.textContent = (profitBB >= 0 ? "+" : "") + profitBB.toFixed(1) + "bb";
    el.style.cssText = `
      left:${rect.left + rect.width / 2}px;top:${rect.top}px;
      transform:translateX(-50%);
      color:${profitBB >= 0 ? "var(--win)" : "var(--lose)"};
      transition:transform 1s ease,opacity 0.8s 0.4s ease;`;
    document.body.appendChild(el);
    requestAnimationFrame(() => requestAnimationFrame(() => {
      el.style.transform = "translateX(-50%) translateY(-44px)";
      el.style.opacity   = "0";
      setTimeout(() => el.remove(), 1600);
    }));
  }

  // Drive the timer ring animation for a seat
  function setTurnTimer(seatIdx, ms) {
    const seatEl = seatEls[seatIdx];
    if (!seatEl) return;
    const arc = seatEl.querySelector(".timer-arc");
    if (!arc) return;

    // Reset all other arcs
    for (const el of seatEls) {
      const a = el?.querySelector(".timer-arc");
      if (!a) continue;
      a.classList.remove("draining", "pulsing");
      a.style.strokeDashoffset = "131.9";
    }

    if (ms === 0) {
      // Hero's turn — pulse
      arc.classList.add("pulsing");
    } else if (ms > 0) {
      // AI — drain over ms
      arc.style.setProperty("--timer-ms", ms + "ms");
      arc.style.strokeDashoffset = "0";
      arc.classList.add("draining");
    }
  }

  function stopAllTimers() {
    for (const el of seatEls) {
      const arc = el?.querySelector(".timer-arc");
      if (!arc) continue;
      arc.classList.remove("draining", "pulsing");
      arc.style.strokeDashoffset = "131.9";
    }
  }

  function updateSeat(seatIdx, updates) {
    const el = seatEls[seatIdx];
    if (!el) return;

    if (updates.name !== undefined) {
      el.querySelector(".seat-name").textContent = updates.name || "";
      el.querySelector(".avatar-initials").textContent = initials(updates.name);
    }
    if (updates.position !== undefined) {
      el.querySelector(".seat-pos").textContent = updates.position || "";
    }
    if (updates.stack !== undefined) {
      el.querySelector(".seat-stack").textContent = updates.stack != null
        ? updates.stack.toFixed(1) + "bb"
        : "";
    }
    if (updates.currentBet !== undefined) {
      renderBet(el, updates.currentBet);
    }

    // Cards
    if (updates.cards !== undefined) {
      const cardsEl = el.querySelector(".seat-cards");
      cardsEl.innerHTML = "";
      if (updates.cards && updates.cards.length > 0) {
        const animate = updates.animate === true;
        for (const c of updates.cards) {
          const cardEl = (c === "?") ? createCardBack(animate) : createCard(c, { animate });
          cardsEl.appendChild(cardEl);
          if (animate) {
            requestAnimationFrame(() => requestAnimationFrame(() => cardEl.classList.remove("dealing")));
          }
        }
      }
    }

    // Fold / allin / winner state
    if (updates.folded !== undefined || updates.allin !== undefined) {
      el.classList.toggle("folded", !!updates.folded);
      const badge = el.querySelector(".seat-badge");
      if (updates.folded) {
        badge.textContent = "FOLD";
        badge.className   = "seat-badge badge-fold";
      } else if (updates.allin) {
        badge.textContent = "ALL-IN";
        badge.className   = "seat-badge badge-allin";
      } else if (updates.folded === false && updates.allin === false) {
        badge.textContent = "";
        badge.className   = "seat-badge";
      }
    }
  }

  function setBoard(cards, animate = false) {
    const boardEl = document.getElementById("board");
    if (!boardEl) return;
    boardEl.innerHTML = "";
    for (const c of cards) {
      const el = createCard(c, { animate });
      boardEl.appendChild(el);
      if (animate) {
        requestAnimationFrame(() => requestAnimationFrame(() => el.classList.remove("dealing")));
      }
    }
  }

  function setPot(amount, bigBlind) {
    const potEl = document.getElementById("potAmt");
    if (potEl) potEl.textContent = (amount / bigBlind).toFixed(1);
  }

  function setDealerBtn(seatIdx) {
    lastDealer = seatIdx;
    if (!dealerEl || !seatEls[seatIdx]) return;
    const seatRect  = seatEls[seatIdx].getBoundingClientRect();
    const tableRect = tableEl.getBoundingClientRect();
    const x = seatRect.left - tableRect.left + seatRect.width  * 0.85;
    const y = seatRect.top  - tableRect.top  - 10;
    dealerEl.style.left = x + "px";
    dealerEl.style.top  = y + "px";
  }

  function showWinner(seatIdx) {
    const el = seatEls[seatIdx];
    if (!el) return;
    el.classList.remove("folded");
    const badge = el.querySelector(".seat-badge");
    badge.textContent = "WINNER";
    badge.className   = "seat-badge badge-winner";
  }

  function clearBadges() {
    for (const el of seatEls) {
      el?.classList.remove("folded");
      const badge = el?.querySelector(".seat-badge");
      if (badge) { badge.textContent = ""; badge.className = "seat-badge"; }
    }
  }

  function handleEvent(evt, state) {
    if (!state) return;
    switch (evt.kind) {
      case "hand_start":
        stopAllTimers();
        clearBadges();
        clearBets();
        for (const el of seatEls) el?.classList.remove("your-turn");
        setBoard([]);
        setPot(0, state.bigBlind);
        if (evt.players && state.seated) {
          for (let i = 0; i < state.seated.length; i++) {
            const p = evt.players[i];
            if (!p) continue;
            const isHuman = state.seated[i].id === "human";
            updateSeat(i, {
              name:       p.name,
              position:   p.position,
              stack:      p.stack,
              cards:      isHuman ? null : ["?", "?"],
              animate:    !isHuman,
              folded:     false,
              allin:      false,
              currentBet: 0,
            });
          }
        }
        break;

      case "turn":
        setTurnTimer(evt.seatIdx, evt.ms ?? 0);
        break;

      case "street":
        animateBetsToPot();
        setBoard(evt.board, true);
        break;

      case "showdown":
        stopAllTimers();
        if (evt.pots) {
          const totalPot = evt.pots.reduce((s, p) => s + p.amount, 0);
          setPot(totalPot, state.bigBlind);
          for (const pot of evt.pots) {
            for (const wi of pot.winners) {
              showWinner(wi);
              animatePotToWinner(wi, pot.amount / state.bigBlind);
            }
          }
        }
        if (evt.summary && state.seated) {
          for (let i = 0; i < evt.summary.length; i++) {
            const s = evt.summary[i];
            if (!s.folded && state.seated[i]?.id !== "human" && s.cards) {
              updateSeat(i, { cards: s.cards });
            }
          }
        }
        break;

      case "action": {
        const si = evt.seatIdx;
        const at = evt.action?.type;
        if (at === "fold") {
          updateSeat(si, { folded: true, allin: false });
        } else if (at === "allin") {
          updateSeat(si, { allin: true });
        }
        const bb = state.bigBlind || 1;
        if (at === "raise" && evt.action.total != null) {
          renderBet(seatEls[si], evt.action.total / bb);
        } else if (at === "blind" && evt.action.amount != null) {
          renderBet(seatEls[si], evt.action.amount / bb);
        } else if (at === "call" && evt.action.amount != null) {
          renderBet(seatEls[si], evt.action.amount / bb);
        } else if (at === "allin" && (evt.action.total ?? evt.action.amount) != null) {
          renderBet(seatEls[si], (evt.action.total ?? evt.action.amount) / bb);
        }
        break;
      }

      case "hero_cards":
        if (state.seated) {
          const heroIdx = state.seated.findIndex(a => a.id === "human");
          if (heroIdx >= 0 && evt.cards) {
            updateSeat(heroIdx, { cards: evt.cards, animate: true });
            seatEls[heroIdx]?.classList.add("your-turn");
          }
        }
        break;

      default: break;
    }
  }

  return {
    init,
    positionSeats,
    updateSeat,
    setBoard,
    setPot,
    setDealerBtn,
    showWinner,
    clearBadges,
    handleEvent,
    showFloatingProfit,
    stopAllTimers,
  };
}
