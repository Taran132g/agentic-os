// DOM + formatting helpers shared by every tab. Untrusted text always goes through textContent.

export const BAT_PATH = "M100 24 103 20 106 9 109 24C120 22 135 12 150 8 170 4 188 10 200 24 186 28 178 38 176 50 168 44 156 44 150 52 144 44 132 42 126 50 120 54 112 64 100 82 88 64 80 54 74 50 68 42 56 44 50 52 44 44 32 44 24 50 22 38 14 28 0 24 12 10 30 4 50 8 65 12 80 22 91 24L94 9 97 20Z";
export const API = "/api/cave";
const SVG_NS = "http://www.w3.org/2000/svg";

export function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export async function api(path, { lockOn401 = true, ...options } = {}) {
  const res = await fetch(`${API}${path}`, { credentials: "same-origin", headers: { "Content-Type": "application/json" }, ...options });
  if (res.status === 401 && lockOn401) {
    document.dispatchEvent(new CustomEvent("pais:locked"));
    throw new Error("locked");
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}

export const money = (v, dp = 2) => {
  if (v == null) return "—";
  const n = Number(v);
  const abs = Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: dp, maximumFractionDigits: dp });
  return n < 0 && Number(abs.replace(/,/g, "")) !== 0 ? `−$${abs}` : `$${abs}`;  // never "$-0.00"
};
export const num = (v, dp = 0) => v == null ? "—" : Number(v).toLocaleString(undefined, { minimumFractionDigits: dp, maximumFractionDigits: dp });
export const pct = (v, dp = 1) => v == null ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(dp)}%`;

export function ago(iso) {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 0) return until(iso);
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 129600) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

export function until(iso) {
  if (!iso) return "—";
  const s = (new Date(iso).getTime() - Date.now()) / 1000;
  if (s <= 0) return "due now";
  if (s < 5400) return `in ${Math.round(s / 60)} min`;
  if (s < 129600) return `in ${Math.round(s / 3600)} h`;
  return `in ${Math.round(s / 86400)} days`;
}

export const when = iso => iso ? new Date(iso).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—";
export const day = iso => iso ? new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "—";

export function light(status) {
  const cls = { done: "ok", armed: "ok", failed: "bad", fault: "bad", offline: "bad", cloud: "cloud", "no report": "warn", "never run": "warn" }[status] || "";
  return h("span", { class: "status-pill" }, h("i", { class: `light ${cls}` }), status);
}

export function card(title, body, { cls = "", action = null } = {}) {
  return h("section", { class: `card ${cls}` },
    h("div", { class: "card-head" }, h("h2", { class: "sect" }, title), action),
    body);
}

export const errorCard = (title, err) => card(title, h("p", { class: "err" }, `Signal lost: ${err}`), { cls: "alert" });

export function head(eyebrow, code, title, lede) {
  return h("div", { class: "head" }, h("div", {},
    h("p", { class: "eyebrow" }, `${eyebrow} · `, h("b", {}, code)),
    h("h1", { class: "title" }, title),
    lede ? h("p", { class: "lede" }, lede) : null));
}

// Goal meter: label, current / target, filled bar. `ratio` may exceed 1 (goal beaten).
export function meter(label, current, target, ratio, { sub = "" } = {}) {
  const r = Math.max(0, ratio ?? 0);
  const state = r >= 1 ? "done" : r >= 0.6 ? "near" : "far";
  return h("div", { class: `meter ${state}` },
    h("div", { class: "meter-top" },
      h("span", { class: "kpi-label" }, label),
      h("span", { class: "mono meter-num" }, h("strong", {}, current), ` / ${target}`)),
    h("div", { class: "meter-track", role: "meter", "aria-label": label, "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(Math.round(Math.min(r, 1) * 100)) },
      h("div", { class: "meter-fill", style: `transform:scaleX(${Math.min(r, 1)})` })),
    sub ? h("div", { class: "kpi-sub" }, sub) : null);
}

function svg(w, hgt, label) {
  const s = document.createElementNS(SVG_NS, "svg");
  s.setAttribute("viewBox", `0 0 ${w} ${hgt}`);
  s.setAttribute("class", "spark");
  s.setAttribute("preserveAspectRatio", "none");
  s.setAttribute("role", "img");
  s.setAttribute("aria-label", label);
  return s;
}

function el(tag, attrs, parent) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  parent.append(node);
  return node;
}

// Single-series bar chart with an optional goal line; each bar gets a native tooltip.
export function barChart(items, { goal = null, label = "", format = v => v } = {}) {
  const W = 600, H = 160, P = 6, GAP = 4;
  const s = svg(W, H, label);
  const max = Math.max(goal || 0, ...items.map(i => i.value), 1);
  const bw = (W - P * 2) / items.length - GAP;
  items.forEach((item, i) => {
    const bh = (item.value / max) * (H - P * 2);
    const bar = el("rect", { x: P + i * (bw + GAP), y: H - P - bh, width: bw, height: Math.max(bh, item.value ? 2 : 0), rx: 3,
      fill: goal != null && item.value >= goal ? "var(--signal)" : "var(--hud)", opacity: item.current ? 1 : 0.6 }, s);
    const title = document.createElementNS(SVG_NS, "title");
    title.textContent = `${item.label}: ${format(item.value)}`;
    bar.append(title);
  });
  if (goal != null) {
    const gy = H - P - (goal / max) * (H - P * 2);
    el("line", { x1: 0, x2: W, y1: gy, y2: gy, stroke: "var(--signal)", "stroke-width": 1, "vector-effect": "non-scaling-stroke", opacity: 0.7 }, s);
  }
  return s;
}

export function table(headers, rows, { numeric = [] } = {}) {
  return h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, headers.map((t, i) => h("th", { class: numeric.includes(i) ? "num" : "" }, t)))),
    h("tbody", {}, rows.map(r => h("tr", {}, r.map((c, i) => h("td", { class: numeric.includes(i) ? "num" : "" }, c)))))));
}
