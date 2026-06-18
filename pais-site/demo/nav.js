/* Shared top nav across all demo pages. Marks the active page and keeps the
   selected business sticky across pages — resolved at CLICK time (from the URL
   or the stored selection), so switching the dropdown on one page carries to
   every other page. */
(function () {
  // Inline Lucide-style icons — stroke: currentColor so they inherit the nav's
  // faint / hover / active-brass colors automatically.
  const svg = (paths) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
  const ICONS = {
    dashboard:  svg('<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/>'),
    winback:    svg('<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-1"/>'),
    voice:      svg('<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2"/><path d="M12 19v3"/>'),
    reputation: svg('<path d="M12 3.2l2.5 5.1 5.6.8-4.05 3.95.96 5.6L12 16.9l-5.01 2.65.96-5.6L3.9 9.1l5.6-.8L12 3.2z"/>'),
    brief:      svg('<path d="M19 20H6a2 2 0 0 1-2-2V5a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v13a2 2 0 0 0 2 2 2 2 0 0 0 2-2V9h-3"/><path d="M8 8h6M8 12h6M8 16h4"/>'),
    insights:   svg('<path d="M3 3v17a1 1 0 0 0 1 1h17"/><rect x="7" y="11" width="3" height="6" rx="0.6"/><rect x="12" y="7" width="3" height="10" rx="0.6"/><rect x="17" y="13" width="3" height="4" rx="0.6"/>'),
  };
  const PAGES = [
    { href: "index.html",        label: "Dashboard",   icon: ICONS.dashboard },
    { href: "reactivation.html", label: "Win-back",    icon: ICONS.winback },
    { href: "voice-demo.html",   label: "Voice",       icon: ICONS.voice },
    { href: "reputation.html",   label: "Reputation",  icon: ICONS.reputation },
    { href: "digest.html",       label: "Daily brief", icon: ICONS.brief },
    { href: "analytics.html",    label: "Insights",    icon: ICONS.insights },
  ];
  // Pages that are NOT business-specific: the voice demo is always the fictional
  // "Copper Lantern".
  const NO_BIZ = new Set(["voice-demo.html"]);
  const here = location.pathname.split("/").pop() || "index.html";

  function currentBiz() {
    return new URLSearchParams(location.search).get("biz") ||
           localStorage.getItem("demoBiz") || "";
  }

  const nav = document.createElement("nav");
  nav.className = "appnav";
  nav.setAttribute("aria-label", "Demo sections");
  const links = PAGES.map(p => {
    const active = p.href === here || (here === "" && p.href === "index.html");
    return `<a class="appnav-link${active ? " is-active" : ""}" data-href="${p.href}" href="${p.href}"><span>${p.icon}</span>${p.label}</a>`;
  }).join("");
  nav.innerHTML = `<a class="appnav-home" href="/" title="Back to PAIS">◓ PAIS</a>${links}`
    + `<a class="appnav-book" href="https://cal.com/taranveer/workflows">Book a call</a>`;

  // Resolve the business at click time so nav always reflects the latest pick.
  nav.addEventListener("click", (e) => {
    const a = e.target.closest(".appnav-link");
    if (!a) return;
    const base = a.dataset.href;
    const biz = currentBiz();
    a.href = (biz && !NO_BIZ.has(base)) ? `${base}?biz=${biz}` : base;
  });

  document.body.insertBefore(nav, document.body.firstChild);

  // On phones the nav scrolls horizontally — bring the active tab into view so
  // users always see where they are. Scrolls only the nav, never the page.
  const activeEl = nav.querySelector(".appnav-link.is-active");
  if (activeEl) nav.scrollLeft = Math.max(0, activeEl.offsetLeft - 64);
})();
