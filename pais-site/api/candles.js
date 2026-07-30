// Serverless candle proxy for the trade chart.
// Pulls OHLCV from Yahoo Finance server-side (no CORS, no key) so the
// Lightweight Charts page can draw candles + volume for crypto, stocks, indices.
export default async function handler(req, res) {
  try {
    const { symbol, interval = "60m", range = "1mo" } = req.query;
    if (!symbol) { res.status(400).json({ error: "symbol required" }); return; }

    const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}` +
      `?interval=${encodeURIComponent(interval)}&range=${encodeURIComponent(range)}&includePrePost=false`;

    const r = await fetch(url, { headers: { "User-Agent": "Mozilla/5.0 (compatible; PAIS-desk/1.0)" } });
    if (!r.ok) { res.status(502).json({ error: "upstream " + r.status }); return; }

    const j = await r.json();
    const result = j && j.chart && j.chart.result && j.chart.result[0];
    if (!result) { res.status(404).json({ error: "no data for " + symbol }); return; }

    const ts = result.timestamp || [];
    const q = (result.indicators && result.indicators.quote && result.indicators.quote[0]) || {};
    const candles = [];
    let hasVolume = false;
    for (let i = 0; i < ts.length; i++) {
      const o = q.open && q.open[i], h = q.high && q.high[i],
            l = q.low && q.low[i], c = q.close && q.close[i], v = q.volume && q.volume[i];
      if (o == null || h == null || l == null || c == null) continue;
      if (v) hasVolume = true;
      candles.push({ time: ts[i], open: o, high: h, low: l, close: c, volume: v || 0 });
    }

    res.setHeader("Cache-Control", "s-maxage=60, stale-while-revalidate=180");
    res.status(200).json({ symbol, interval, range, hasVolume, candles });
  } catch (e) {
    res.status(500).json({ error: String((e && e.message) || e) });
  }
}
