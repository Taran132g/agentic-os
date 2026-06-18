/* ============================================================
   Google insights dashboard — render per-business analytics.
   CSS-only / inline-SVG charts (no library) per perf budget.
   ============================================================ */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const fmt = (n) => Number(n).toLocaleString("en-US");

let A; // active business analytics

function pickBiz() {
  const url = new URLSearchParams(location.search).get("biz");
  const stored = localStorage.getItem("demoBiz");
  const b = (url && ANALYTICS[url]) ? url : (stored && ANALYTICS[stored]) ? stored : "trappe-tavern";
  localStorage.setItem("demoBiz", b);
  return b;
}

function countUp(el, to) {
  const dur = 1000, start = performance.now();
  function tick(now) {
    const p = Math.min(1, (now - start) / dur);
    el.textContent = fmt(Math.round(to * (1 - Math.pow(1 - p, 3))));
    if (p < 1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

/* ---------- KPI cards ---------- */
function renderKPIs() {
  const k = A.kpis;
  const card = (label, o, hint) => `
    <div class="kpi">
      <span class="kpi-label">${label}</span>
      <span class="kpi-num" data-to="${o.value}">0</span>
      <span class="kpi-delta ${o.delta >= 0 ? "up" : "down"}">${o.delta >= 0 ? "▲" : "▼"} ${Math.abs(o.delta)}% <small>vs last month</small></span>
      <span class="kpi-hint">${hint}</span>
    </div>`;
  $("#kpiRow").innerHTML =
    card("Searches", k.searches, "times you appeared in Google") +
    card("Profile views", k.views, "people who looked at your listing") +
    card("Customer actions", k.actions, "calls, directions, clicks");
  $$(".kpi-num").forEach(el => countUp(el, +el.dataset.to));
}

/* ---------- line chart (inline SVG) ---------- */
function renderLineChart() {
  const data = A.trend, max = Math.max(...data), min = Math.min(...data);
  const W = 100, H = 38, pad = 2;
  const x = i => (i / (data.length - 1)) * W;
  const y = v => H - pad - ((v - min) / (max - min || 1)) * (H - pad * 2);
  const pts = data.map((v, i) => `${x(i).toFixed(2)},${y(v).toFixed(2)}`);
  const line = "M" + pts.join(" L");
  const area = `${line} L${W},${H} L0,${H} Z`;
  const last = data.length - 1;
  $("#lineChart").innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="lc-svg">
      <defs><linearGradient id="lcfill" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="var(--brass)" stop-opacity="0.34"/>
        <stop offset="100%" stop-color="var(--brass)" stop-opacity="0"/>
      </linearGradient></defs>
      <path d="${area}" fill="url(#lcfill)"/>
      <path d="${line}" fill="none" stroke="var(--brass)" stroke-width="0.7" stroke-linejoin="round" stroke-linecap="round" class="lc-line"/>
      <circle cx="${x(last).toFixed(2)}" cy="${y(data[last]).toFixed(2)}" r="1" fill="var(--brass)"/>
    </svg>`;
  $("#trendTotal").innerHTML = `${fmt(data.reduce((a, b) => a + b, 0))} <small>views</small>`;
}

/* ---------- donut (conic-gradient) ---------- */
function renderSearchDonut() {
  const items = A.searchTypes, total = items.reduce((a, b) => a + b.value, 0);
  let acc = 0;
  const stops = items.map(it => {
    const start = (acc / total) * 360; acc += it.value;
    const end = (acc / total) * 360;
    return `${it.color} ${start}deg ${end}deg`;
  }).join(", ");
  $("#searchDonut").style.background = `conic-gradient(${stops})`;
  $("#searchDonut").innerHTML = `<div class="donut-hole"><span>${fmt(total)}</span><small>searches</small></div>`;
  $("#searchLegend").innerHTML = items.map(it =>
    `<li><span class="dot" style="background:${it.color}"></span>${it.label}
      <b>${Math.round(it.value / total * 100)}%</b><small>${fmt(it.value)}</small></li>`).join("");
}

/* ---------- platform split ---------- */
function renderPlatform() {
  const p = A.platform, total = p.search + p.maps;
  const sPct = Math.round(p.search / total * 100), mPct = 100 - sPct;
  $("#platformSplit").innerHTML = `
    <div class="split-seg search" style="width:${sPct}%"><span>Search</span><b>${sPct}%</b></div>
    <div class="split-seg maps" style="width:${mPct}%"><span>Maps</span><b>${mPct}%</b></div>`;
  $("#platformNote").innerHTML = p.maps > p.search
    ? `Most people find you on <b>Maps</b> — directions &amp; "near me" drive your foot traffic.`
    : `Most people find you on <b>Search</b> — they're looking you up directly before deciding.`;
}

/* ---------- top queries ---------- */
function renderQueries() {
  const max = Math.max(...A.topQueries.map(q => q.count));
  $("#queryList").innerHTML = A.topQueries.map(q => `
    <li class="query">
      <span class="query-q">${q.q}</span>
      <span class="query-bar"><i style="width:${(q.count / max * 100).toFixed(0)}%"></i></span>
      <span class="query-n">${fmt(q.count)}</span>
    </li>`).join("");
}

/* ---------- action bars ---------- */
function renderActions() {
  const max = Math.max(...A.actions.map(a => a.value));
  $("#actionBars").innerHTML = A.actions.map(a => `
    <li class="abar">
      <span class="abar-label">${a.label}${a.tie ? ` <em class="abar-tag">→ Missed-Call / Voice</em>` : ""}</span>
      <span class="abar-track"><i style="width:${(a.value / max * 100).toFixed(0)}%;background:${a.color}"></i></span>
      <span class="abar-val">${fmt(a.value)}</span>
    </li>`).join("");
}

/* ---------- insights ---------- */
function renderInsights() {
  $("#insight1").innerHTML = `<span class="insight-ic">💡</span><p>${A.insights[0]}</p>`;
  $("#insight2").innerHTML = `<span class="insight-ic">💡</span><p>${A.insights[1]}</p>`;
}

/* ---------- reviews ---------- */
function renderReviews() {
  const r = A.reviews, full = Math.floor(r.rating), half = r.rating - full >= 0.3;
  let stars = "";
  for (let i = 0; i < 5; i++) stars += `<span class="star ${i < full ? "on" : (i === full && half ? "half" : "")}">★</span>`;
  $("#reviewCard").innerHTML = `
    <div class="rev-score">
      <span class="rev-num">${r.rating}</span>
      <div class="rev-stars">${stars}</div>
      <span class="rev-count">${fmt(r.count)} reviews</span>
    </div>
    <div class="rev-meta">
      <div class="rev-stat"><b>+${r.last30}</b><span>new this month</span></div>
      <div class="rev-stat"><b>${r.replied}</b><span>replied by your Reputation agent</span></div>
    </div>
    <p class="rev-tie">Every new review gets an owner-approved reply within the day, and happy customers get asked — that's how the score keeps climbing.</p>`;
}

/* ---------- chrome + nav ---------- */
function renderChrome() {
  document.body.dataset.theme = A.theme;
  $("#bizName").textContent = A.label;
  $("#bizSub").textContent = A.sub;
  $("#bizSwitch").value = A._id;
  const dl = document.querySelector("#digestLink");
  if (dl) dl.href = `digest.html?biz=${A._id}`;
  document.title = `${A.label} · Google Insights`;
}

function load(id) {
  A = ANALYTICS[id]; A._id = id;
  renderChrome();
  renderKPIs();
  renderLineChart();
  renderSearchDonut();
  renderPlatform();
  renderQueries();
  renderActions();
  renderInsights();
  renderReviews();
}

function wireNav() {
  $$(".subnav-link").forEach(a => a.addEventListener("click", e => {
    e.preventDefault();
    $(a.getAttribute("href")).scrollIntoView({ behavior: "smooth", block: "start" });
  }));
  const obs = new IntersectionObserver(entries => {
    entries.forEach(en => {
      if (en.isIntersecting) {
        $$(".subnav-link").forEach(l => l.classList.toggle("is-active", l.getAttribute("href") === "#" + en.target.id));
      }
    });
  }, { rootMargin: "-45% 0px -50% 0px" });
  $$(".ana-section").forEach(s => obs.observe(s));
}

function init() {
  $("#bizSwitch").addEventListener("change", e => {
    localStorage.setItem("demoBiz", e.target.value);
    const url = new URL(location); url.searchParams.set("biz", e.target.value);
    history.replaceState({}, "", url);
    load(e.target.value);
  });
  wireNav();
  load(pickBiz());
}

document.addEventListener("DOMContentLoaded", init);
