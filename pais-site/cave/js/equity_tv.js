// Trading-style portfolio equity chart (TradingView Lightweight Charts, vendored under /cave/vendor, Apache-2.0).
// One persistent instance: 20 s refreshes update the data without resetting zoom/scroll.
import { api, h, money } from "./ui.js";

export const TIMEFRAMES = [["1m", "1m"], ["5m", "5m"], ["15m", "15m"], ["1h", "1H"], ["4h", "4H"], ["1d", "1D"]];
const TF_KEY = "pais-equity-tf";
const UP = "#0ecb81";
const DOWN = "#f6465d";
const TZ_SHIFT = -new Date().getTimezoneOffset() * 60;  // library renders UTC; shift so axis shows local time

let state = null;  // { card, chart, series, tf, lines, legend, lastCandles }

const savedTf = () => { try { return localStorage.getItem(TF_KEY) || "15m"; } catch { return "15m"; } };
const toLocal = (c, tf) => (tf === "1d" ? c : { ...c, time: c.time + TZ_SHIFT });
const fmtUsd = v => money(v, 2);

function legendText(c, prev) {
  if (!c) return "";
  const change = prev ? c.close - prev.close : 0;
  const pctChg = prev ? (change / prev.close) * 100 : 0;
  return `O ${fmtUsd(c.open)}  H ${fmtUsd(c.high)}  L ${fmtUsd(c.low)}  C ${fmtUsd(c.close)}  ${change >= 0 ? "+" : "−"}${fmtUsd(Math.abs(change)).slice(1)} (${pctChg >= 0 ? "+" : ""}${pctChg.toFixed(2)}%)`;
}

function build() {
  const LC = window.LightweightCharts;
  const host = h("div", { class: "tv-host" });
  const legend = h("div", { class: "tv-legend mono" });
  const tfBar = h("div", { class: "tv-tfs", role: "group", "aria-label": "Timeframe" });
  const card = h("section", { class: "card tv-card" },
    h("div", { class: "tv-head" },
      h("div", {}, h("h2", { class: "sect" }, "Portfolio equity"), legend),
      tfBar),
    host);
  const chart = LC.createChart(host, {
    autoSize: true,
    layout: { background: { type: "solid", color: "transparent" }, textColor: "#e6edf3", fontFamily: "JetBrains Mono, monospace", fontSize: 11 },
    grid: { vertLines: { color: "rgba(255,255,255,0.04)" }, horzLines: { color: "rgba(255,255,255,0.06)" } },
    crosshair: { mode: LC.CrosshairMode.Normal },
    rightPriceScale: { borderColor: "rgba(255,255,255,0.12)", scaleMargins: { top: 0.12, bottom: 0.12 } },
    timeScale: { borderColor: "rgba(255,255,255,0.12)", timeVisible: true, secondsVisible: false, rightOffset: 4 },
  });
  const series = chart.addCandlestickSeries({
    upColor: UP, downColor: DOWN, borderUpColor: UP, borderDownColor: DOWN, wickUpColor: UP, wickDownColor: DOWN,
    priceFormat: { type: "price", precision: 2, minMove: 0.01 },
  });
  state = { card, chart, series, tf: savedTf(), lines: {}, legend, tfBar, candles: [] };
  chart.subscribeCrosshairMove(param => {
    const c = param.time ? param.seriesData.get(series) : null;
    const i = c ? state.candles.findIndex(x => x.time === c.time) : -1;
    legend.textContent = c ? legendText(c, state.candles[i - 1]) : legendText(state.candles.at(-1), state.candles.at(-2));
  });
  renderTfs();
}

function renderTfs() {
  state.tfBar.replaceChildren(...TIMEFRAMES.map(([tf, label]) => h("button", {
    class: "tv-tf", type: "button", "aria-pressed": String(state.tf === tf),
    onclick: () => { if (state.tf !== tf) { state.tf = tf; try { localStorage.setItem(TF_KEY, tf); } catch { /* ok */ } renderTfs(); load(true); } },
  }, label)));
}

let pendingLive = null;

async function load(fit = false) {
  const { tf } = state;
  const res = await api(`/candles?tf=${tf}`);
  if (!state || state.tf !== tf) return;  // timeframe changed mid-request
  state.candles = res.candles.map(c => toLocal(c, tf));
  state.series.setData(state.candles);
  if (pendingLive) applyLive(pendingLive);
  state.legend.textContent = legendText(state.candles.at(-1), state.candles.at(-2));
  if (fit) state.chart.timeScale().fitContent();
}

// Close the current candle at the page's live equity so the chart ends on the same number as the tiles.
function applyLive(v) {
  const last = state.candles.at(-1);
  if (!last || v.equity == null) return;
  const bar = { ...last, close: v.equity, high: Math.max(last.high, v.equity), low: Math.min(last.low, v.equity) };
  state.candles[state.candles.length - 1] = bar;
  state.series.update(bar);
}

function setLines(v) {
  const lines = [["start", v.month_start, "#8b949e", "month start"], ["goal", v.target_balance, "#f3c431", "goal"]];
  for (const [key, price, color, title] of lines) {
    if (price == null) continue;
    if (state.lines[key]) state.lines[key].applyOptions({ price });
    else state.lines[key] = state.series.createPriceLine({ price, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title });
  }
}

/** Returns the chart card (same node every time) and refreshes it with the latest numbers. */
export function equityCard(v, { first = false } = {}) {
  if (!window.LightweightCharts) return h("section", { class: "card" }, h("p", { class: "err" }, "Chart library failed to load."));
  if (!state || first) { destroy(); build(); load(true).catch(() => {}); }
  else load(false).catch(() => {});
  pendingLive = v;
  setLines(v);
  return state.card;
}

export function destroy() {
  if (state) state.chart.remove();
  state = null;
  pendingLive = null;
}
