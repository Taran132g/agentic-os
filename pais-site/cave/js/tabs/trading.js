// 02 TRADING — monthly return goal on the Yubit account, marked to live prices.
// The daily Yubit run supplies balance + positions (and one equity point per day);
// between runs every number is re-marked at live prices so they always agree:
// equity = balance + Σ open P/L, MTD = equity / month start − 1.
import { say } from "../alfred.js";
import { equityCard, destroy as destroyChart } from "../equity_tv.js";
import { api, h, card, head, money, num, pct, meter, table, when, ago, day, errorCard } from "../ui.js";

const POLL_MS = 20_000;  // balances, prices and the live equity line all refresh on this beat
const CARD_PADDING = 56;  // main + card padding per side (desktop); CSS scales the rest
let timer = null;
let live = null;
let coinbase = null;  // {…summary} or {error}
let firstDraw = true;  // build the chart once per visit; later draws reuse it (keeps zoom/scroll)

// Traders whose calls you follow. Dr. Profit's Telegram calls also feed the PAIS listener on Oracle.
const SOURCES = [
  { name: "Dr. Profit", handle: "DrProfitCrypto", note: "Swing calls on BTC and majors; his Telegram signals feed your Dr. Profit listener." },
  { name: "DiligentPlane", handle: "DiligentPlane", note: "" },
  { name: "No Limit Gains", handle: "NoLimitGains", note: "" },
  { name: "Jason Pizzino", handle: "jasonpizzino", note: "Macro and market-cycle takes." },
  { name: "Kevin Xu", handle: "kevinxu", note: "" },
];


// Whole-portfolio view: Yubit (live if priced) + Coinbase, against the combined month start.
function portfolio(r, y) {
  const cb = coinbase && !coinbase.error ? coinbase : null;
  if (!cb || cb.month_start == null || r.month_start == null) {
    return { combined: false, ...y, month_start: r.month_start, yubit: y };
  }
  const start = r.month_start + cb.month_start;
  const equity = (y.equity || 0) + cb.total;
  const target = r.target_pct || 20;
  const mtd = (equity / start - 1) * 100;
  const targetBalance = start * (1 + target / 100);
  return {
    combined: true, yubit: y, coinbase: cb, month_start: start, equity, mtd_pct: mtd,
    unrealized_pnl: (y.unrealized_pnl || 0) + (cb.unrealized_pnl || 0),
    progress: mtd / target, hit: mtd >= target, target_balance: targetBalance,
    remaining_usd: Math.max(0, targetBalance - equity), positions: y.positions,
  };
}

function liveBadge(r) {
  if (!live) return h("span", { class: "live-badge off" }, `Last real read ${ago(r.as_of)}`);
  return h("span", { class: "live-badge" }, h("i", { class: "light ok" }), `LIVE · priced ${new Date(live.priced_at).toLocaleTimeString()}`);
}

function hero(r, v) {
  return h("section", { class: `card roi-hero ${v.hit ? "win" : ""}` },
    h("div", {},
      h("div", { class: "kpi-label" }, `${v.combined ? "Entire portfolio" : "Yubit"} · ${new Date(`${r.month}-01T12:00:00`).toLocaleDateString(undefined, { month: "long", year: "numeric" })} · month to date`),
      h("div", { class: `kpi-value hero ${v.mtd_pct >= 0 ? "up" : "down"}` }, pct(v.mtd_pct, 2)),
      h("div", { class: "kpi-sub" }, v.hit
        ? `Goal of +${r.target_pct}% cleared by ${num(v.mtd_pct - r.target_pct, 1)} points.`
        : `${money(v.remaining_usd)} to go · ${r.days_left} days left`),
      h("div", { style: "margin-top:10px" }, liveBadge(r))),
    h("div", { class: "roi-meter" },
      meter("Progress to goal", pct(v.mtd_pct, 1), `+${r.target_pct}%`, v.progress, { sub: `Target balance ${money(v.target_balance)}` })));
}

function tiles(r, v) {
  const tile = (label, value, sub, cls = "") => card(label, h("div", {},
    h("div", { class: `kpi-value ${cls}` }, value), sub ? h("div", { class: "kpi-sub" }, sub) : null));
  return h("div", { class: "grid g-4" },
    tile("Month start", money(v.month_start),
      v.combined ? `Yubit ${money(r.month_start, 0)} + Coinbase ${money(v.coinbase.month_start, 0)}` : `from ${r.month_start_source}`),
    tile("Equity", money(v.equity), v.combined ? (live ? "Yubit live + Coinbase" : "Yubit daily read + Coinbase") : (live ? "balance + live open P/L" : "at last daily read")),
    tile("Open P/L", money(v.unrealized_pnl), `${(v.positions || []).length} Yubit positions${v.combined ? " + Coinbase" : ""}`, (v.unrealized_pnl || 0) >= 0 ? "up" : "down"),
    tile("Target balance", money(v.target_balance), v.hit ? "reached" : `${money(v.remaining_usd)} to go`));
}

function accountsCard(r, v) {
  if (!v.combined) return null;
  const row = (name, start, now) => [h("strong", {}, name), money(start), money(now),
    h("span", { class: now >= start ? "up" : "down" }, pct((now / start - 1) * 100))];
  return card("All trading accounts", table(["Account", "Month start", "Now", "Return"], [
    row("Yubit", r.month_start, v.yubit.equity),
    row("Coinbase", v.coinbase.month_start, v.coinbase.total),
    [h("strong", {}, "Combined"), h("strong", {}, money(v.month_start)), h("strong", {}, money(v.equity)),
      h("strong", { class: v.mtd_pct >= 0 ? "up" : "down" }, pct(v.mtd_pct))],
  ], { numeric: [1, 2, 3] }), { cls: "hud" });
}

function coinbaseCard() {
  if (!coinbase) return card("Coinbase", h("p", { class: "empty" }, "Loading Coinbase…"));
  if (coinbase.error) return card("Coinbase", h("p", { class: "err" }, coinbase.error), { cls: "alert" });
  const c = coinbase;
  const stat = (label, value, cls = "") => h("div", {}, h("div", { class: "kpi-label" }, label), h("div", { class: `kpi-value ${cls}` }, value));
  return card(`Coinbase · ${c.portfolio} portfolio`, h("div", {},
    h("div", { class: "grid g-4" }, stat("Total", money(c.total)), stat("Cash", money(c.cash)),
      stat("Stocks", money(c.stocks)), stat("Open P/L", money(c.unrealized_pnl), c.unrealized_pnl >= 0 ? "up" : "down")),
    h("div", { style: "margin-top:14px" }, table(["Holding", "Type", "Qty", "Avg cost", "Price", "Value", "P/L"],
      c.holdings.map(x => [h("strong", {}, x.name), x.kind, x.kind === "cash" ? "—" : num(x.qty, x.kind === "crypto" ? 6 : 2),
        x.avg_entry ? money(x.avg_entry) : "—", x.price ? money(x.price) : "—", money(x.value),
        x.kind === "cash" ? "—" : h("span", { class: x.unrealized_pnl >= 0 ? "up" : "down" }, money(x.unrealized_pnl))]),
      { numeric: [2, 3, 4, 5, 6] })),
    h("p", { class: "kpi-sub" }, `View-only key · refreshed ${new Date(c.as_of).toLocaleTimeString()}`
      + (c.derivatives ? ` · ${c.derivatives} futures positions` : ""))), { cls: "hud" });
}

function sourcesCard() {
  return card("Signal sources", h("div", { class: "sources" }, SOURCES.map(src =>
    h("a", { class: "source", href: `https://x.com/${src.handle}`, target: "_blank", rel: "noopener noreferrer" },
      h("span", { class: "source-name" }, src.name),
      h("span", { class: "mono source-handle" }, `@${src.handle} ↗`),
      src.note ? h("span", { class: "kpi-sub" }, src.note) : null))));
}

// Mon + Fri 9am the Mac reads these sources' X posts and Claude summarizes where each sees the market going.
const BIAS_CLASS = { bullish: "long", bearish: "short", mixed: "flag", neutral: "" };
const biasTag = b => b ? h("span", { class: `tag ${BIAS_CLASS[b] ?? ""}` }, b) : h("span", { class: "muted" }, "—");

function outlookSource(s) {
  return h("div", { class: "outlook-src" },
    h("div", { class: "outlook-src-head" },
      h("a", { class: "source-name", href: `https://x.com/${s.handle}`, target: "_blank", rel: "noopener noreferrer" }, s.name),
      biasTag(s.bias)),
    s.error ? h("p", { class: "err" }, s.error) : h("p", { class: "kpi-sub" }, s.summary),
    s.levels?.length ? h("p", { class: "mono outlook-levels" }, s.levels.join(" · ")) : null,
    ...(s.quotes || []).map(q => h("a", { class: "outlook-quote", href: q.url, target: "_blank", rel: "noopener noreferrer" }, `“${q.text}”`)),
    h("span", { class: "muted outlook-count" }, `${s.post_count} post${s.post_count === 1 ? "" : "s"}`));
}

function outlookHistory(o) {
  if ((o.history || []).length < 2) return null;
  return h("div", { style: "margin-top:16px" }, table(["Run", "Overall", ...SOURCES.map(s => s.name)],
    o.history.map(r => [when(r.until), biasTag(r.overall), ...SOURCES.map(s => biasTag(r.sources?.[s.handle]))])));
}

function outlookCard(o) {
  const title = "Market outlook · from X";
  if (!o || o.status === "awaiting data") return card(title, h("p", { class: "empty" }, "First read runs Monday or Friday at 9am."));
  if (o.status === "error" && !o.overall) return card(title, h("p", { class: "err" }, o.error || o.last_attempt?.error || "Outlook run failed."), { cls: "alert" });
  const failed = o.last_attempt && o.last_attempt.at > o.generated_at ? o.last_attempt : null;
  return card(title, h("div", {},
    h("div", { class: "outlook-overall" }, h("span", { class: "kpi-label" }, "Overall"), biasTag(o.overall.bias)),
    h("p", {}, o.overall.summary),
    o.overall.agree ? h("p", { class: "kpi-sub" }, h("strong", {}, "Agree: "), o.overall.agree) : null,
    o.overall.disagree ? h("p", { class: "kpi-sub" }, h("strong", {}, "Disagree: "), o.overall.disagree) : null,
    h("div", { class: "sources", style: "margin-top:14px" }, o.sources.map(outlookSource)),
    outlookHistory(o),
    h("p", { class: "kpi-sub" }, `${o.post_count} posts from ${when(o.since)} to ${when(o.until)} · runs Mon + Fri 9am`),
    failed ? h("p", { class: "err" }, `Latest run failed ${ago(failed.at)}: ${failed.error}`) : null), { cls: "hud" });
}

function positionsTable(positions) {
  return table(["Symbol", "Side", "Qty", "Entry", "Mark", "Lev", "Open P/L", "Stop", "Target"],
    positions.map(p => [
      h("span", {}, h("strong", {}, p.symbol.replace(/USDT$/, "")), " ", h("span", { class: "muted" }, p.account)),
      h("span", { class: `tag ${p.side}` }, p.side),
      num(p.qty, 4), num(p.entry, 2),
      h("span", { title: p.marked_live ? "live price" : "last Yubit read" }, num(p.mark, 2), p.marked_live === false ? " *" : ""),
      p.leverage ? `${num(p.leverage)}×` : "—",
      h("span", { class: (p.upnl || 0) >= 0 ? "up" : "down" }, `${money(p.upnl)}${p.upnl_pct != null ? ` (${pct(p.upnl_pct)})` : ""}`),
      p.sl == null ? h("span", { class: "tag flag" }, "no stop") : num(p.sl, 2),
      p.tp == null ? "—" : num(p.tp, 2)]),
    { numeric: [2, 3, 4, 5, 6, 7, 8] });
}

function draw(view, snap) {
  const r = snap.roi || {};
  view.replaceChildren();
  view.append(head("Trading", `+${r.target_pct ?? 20}% / month`, "The trading floor",
    r.as_of ? `Daily Yubit audit read the account ${when(r.as_of)}. Prices refresh every 20 seconds.` : null));
  if (r.status === "error") { view.append(errorCard("Trading", r.error)); return; }
  if (r.status !== "ok" || r.month_start == null) {
    view.append(card("Trading", h("p", { class: "empty" }, "Waiting on the next daily Yubit run for a month-start figure.")));
    return;
  }
  // One consistent set of numbers: live if we have prices, otherwise the daily read.
  const y = live
    ? { ...live, equity: live.equity_usd }
    : { equity: r.equity, unrealized_pnl: r.unrealized_pnl, positions: r.positions, mtd_pct: r.mtd_pct,
        progress: r.progress, hit: r.hit, target_balance: r.target_balance, remaining_usd: r.remaining_usd };
  const v = portfolio(r, y);
  view.append(hero(r, v));
  if (live?.flags?.length) {
    view.append(h("div", { style: "margin-top:14px" }, card("Heads up", h("ul", { class: "report" }, live.flags.map(f => h("li", {}, f))), { cls: "alert" })));
  }
  view.append(h("div", { style: "margin-top:14px" }, tiles(r, v)));
  const accounts = accountsCard(r, v);
  if (accounts) view.append(h("div", { style: "margin-top:14px" }, accounts));
  view.append(h("div", { style: "margin-top:14px" }, equityCard(v, { first: firstDraw })));
  firstDraw = false;
  view.append(h("div", { class: "grid g-hero", style: "margin-top:14px" },
    card(`Yubit open positions · ${(v.positions || []).length}`, h("div", {}, positionsTable(v.positions || []),
      live?.unpriced?.length ? h("p", { class: "kpi-sub" }, `* no live price for ${live.unpriced.join(", ")}; showing the last Yubit read`) : null)),
    card("Months", (r.history || []).length
      ? table(["Month", "Start", "End", "Return"], r.history.map(m => m.month === r.month
        ? [`${m.month} (now)`, money(v.month_start, 0), money(v.equity, 0), pct(v.mtd_pct)]
        : [m.month, money(m.start, 0), money(m.end, 0), pct(m.return_pct)]), { numeric: [1, 2, 3] })
      : h("p", { class: "empty" }, "History starts this month."))));
  view.append(h("div", { style: "margin-top:14px" }, outlookCard(snap.outlook)));
  view.append(h("div", { style: "margin-top:14px" }, coinbaseCard()));
  view.append(h("div", { style: "margin-top:14px" }, sourcesCard()));
}

function line(r, v) {
  if (v.mtd_pct == null) return "I'll have this month's figures after the next morning run, sir.";
  const tail = `${v.combined ? "across the whole portfolio " : ""}${live ? "at live prices" : "as of this morning's read"}`;
  return v.hit
    ? `You're up ${Math.round(v.mtd_pct)} percent this month ${tail}, sir. The 20 percent goal is behind you.`
    : `Up ${v.mtd_pct.toFixed(1)} percent this month ${tail}, with ${r.days_left} days left.`;
}

let announce = false;

async function refresh(view, snap) {
  const [liveRes, cbRes] = await Promise.allSettled([api("/live"), api("/coinbase")]);
  if ([liveRes, cbRes].some(r => r.status === "rejected" && r.reason?.message === "locked")) return;
  live = liveRes.status === "fulfilled" ? liveRes.value : null;  // fall back to the daily read, never mixed numbers
  coinbase = cbRes.status === "fulfilled" ? cbRes.value : { error: cbRes.reason?.message || "Coinbase unavailable" };
  if (!view.isConnected) return;
  draw(view, snap);
  if (announce) {
    announce = false;
    const r = snap.roi || {};
    const y = live ? { ...live, equity: live.equity_usd } : { ...r };
    say(line(r, portfolio(r, y)));
  }
}

export default {
  id: "trading", label: "TRADING",
  render(view, snap, { speak = true } = {}) {
    firstDraw = true;
    draw(view, snap);
    announce = speak;  // spoken after the first live price, so Alfred matches the screen
    refresh(view, snap);
    clearInterval(timer);
    timer = setInterval(() => { if (!document.hidden) refresh(view, snap); }, POLL_MS);
    return null;
  },
  leave() {
    clearInterval(timer);
    timer = null;
    destroyChart();
  },
};
