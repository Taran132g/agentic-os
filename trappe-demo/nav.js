/* Shared top nav across all demo pages. Marks the active page and keeps the
   selected business sticky across pages — resolved at CLICK time (from the URL
   or the stored selection), so switching the dropdown on one page carries to
   every other page. */
(function () {
  const PAGES = [
    { href: "index.html",        label: "Dashboard",   icon: "▦" },
    { href: "reactivation.html", label: "Win-back",    icon: "↩" },
    { href: "voice-demo.html",   label: "Voice",       icon: "🎙" },
    { href: "digest.html",       label: "Daily brief", icon: "📰" },
    { href: "analytics.html",    label: "Insights",    icon: "📊" },
    { href: "script.html",       label: "Script",      icon: "📋" },
  ];
  // Pages that are NOT business-specific: the voice demo is always the fictional
  // "Copper Lantern", and the script is the generic playbook.
  const NO_BIZ = new Set(["voice-demo.html", "script.html"]);
  const here = location.pathname.split("/").pop() || "index.html";

  function currentBiz() {
    return new URLSearchParams(location.search).get("biz") ||
           localStorage.getItem("demoBiz") || "";
  }

  const nav = document.createElement("nav");
  nav.className = "appnav";
  nav.setAttribute("aria-label", "Demo sections");
  nav.innerHTML = PAGES.map(p => {
    const active = p.href === here || (here === "" && p.href === "index.html");
    return `<a class="appnav-link${active ? " is-active" : ""}" data-href="${p.href}" href="${p.href}"><span>${p.icon}</span>${p.label}</a>`;
  }).join("");

  // Resolve the business at click time so nav always reflects the latest pick.
  nav.addEventListener("click", (e) => {
    const a = e.target.closest(".appnav-link");
    if (!a) return;
    const base = a.dataset.href;
    const biz = currentBiz();
    a.href = (biz && !NO_BIZ.has(base)) ? `${base}?biz=${biz}` : base;
  });

  document.body.insertBefore(nav, document.body.firstChild);
})();
