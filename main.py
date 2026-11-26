import os
import time
import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="XAUUSD 9-TF Cloud Matrix")

API_KEY = os.getenv("TWELVEDATA_API_KEY", "YOUR_TWELVEDATA_API_KEY")
SYMBOL = "XAU/USD"

# (Label, TwelveData Interval, Active Threshold $, Cache TTL seconds)
TIMEFRAMES = [
    ("1m",  "1min",   1.5,  30),
    ("5m",  "5min",   2.0,  60),
    ("15m", "15min",  2.5,  180),
    ("1H",  "1h",     3.5,  600),
    ("4H",  "4h",     5.0,  1800),
    ("1D",  "1day",   8.0,  3600),
    ("1W",  "1week",  12.0, 14400),
    ("1M",  "1month", 18.0, 21600),
]

CACHE = {}

def calc_pivots(h: float, l: float, c: float, live_price: float, threshold: float):
    p = (h + l + c) / 3.0
    r1 = (2.0 * p) - l
    r2 = p + (h - l)
    r3 = h + 2.0 * (p - l)
    s1 = (2.0 * p) - h
    s2 = p - (h - l)
    s3 = l - 2.0 * (h - p)

    levels = {
        "R3": round(r3, 2), "R2": round(r2, 2), "R1": round(r1, 2),
        "EQ": round(p, 2),
        "S1": round(s1, 2), "S2": round(s2, 2), "S3": round(s3, 2),
    }

    closest = min(levels, key=lambda k: abs(live_price - levels[k]))
    dist = round(live_price - levels[closest], 2)
    is_active = abs(dist) <= threshold

    if is_active:
        if closest.startswith("R"):
            status = f"ACTIVE RESISTANCE ({closest} @ {levels[closest]})"
        elif closest.startswith("S"):
            status = f"ACTIVE SUPPORT ({closest} @ {levels[closest]})"
        else:
            status = f"AT EQUILIBRIUM ({levels['EQ']})"
    else:
        status = f"BULLISH (> EQ {levels['EQ']})" if live_price > p else f"BEARISH (< EQ {levels['EQ']})"

    return {
        "levels": levels,
        "closest": closest,
        "is_active": is_active,
        "status": status,
        "bias": "BULLISH" if live_price >= p else "BEARISH",
    }

async def fetch_series(client: httpx.AsyncClient, interval: str, outputsize: int, ttl: int):
    now = time.time()
    key = f"{interval}_{outputsize}"
    if key in CACHE and (now - CACHE[key]["ts"]) < ttl:
        return CACHE[key]["data"]

    url = "https://api.twelvedata.com/time_series"
    params = {"symbol": SYMBOL, "interval": interval, "outputsize": outputsize, "apikey": API_KEY}
    r = await client.get(url, params=params, timeout=10.0)
    data = r.json()
    if "values" in data:
        CACHE[key] = {"ts": now, "data": data["values"]}
        return data["values"]
    return CACHE.get(key, {}).get("data")

@app.get("/api/levels")
async def get_levels():
    if API_KEY == "YOUR_TWELVEDATA_API_KEY":
        return {"error": "Set TWELVEDATA_API_KEY in Render Environment Variables"}

    async with httpx.AsyncClient() as client:
        # Live price (cached 15s to protect daily credit quota)
        now = time.time()
        if "price" in CACHE and (now - CACHE["price"]["ts"]) < 15:
            live_price = CACHE["price"]["data"]
        else:
            pr = await client.get(
                "https://api.twelvedata.com/price",
                params={"symbol": SYMBOL, "apikey": API_KEY},
                timeout=10.0,
            )
            pdata = pr.json()
            if "price" not in pdata:
                return {"error": pdata.get("message", "API rate limit reached; wait 60s")}
            live_price = round(float(pdata["price"]), 2)
            CACHE["price"] = {"ts": now, "data": live_price}

        results = []
        for label, interval, thresh, ttl in TIMEFRAMES:
            # outputsize=2: index 0 is current open candle, index 1 is last CLOSED candle
            vals = await fetch_series(client, interval, 2, ttl)
            if vals and len(vals) >= 2:
                closed = vals[1]
                row = calc_pivots(float(closed["high"]), float(closed["low"]), float(closed["close"]), live_price, thresh)
                row["tf"] = label
                results.append(row)

        # 2M synthetic candle from last 2 closed monthly candles (indices 1 and 2)
        mn_vals = await fetch_series(client, "1month", 3, 21600)
        if mn_vals and len(mn_vals) >= 3:
            h_2m = max(float(mn_vals[1]["high"]), float(mn_vals[2]["high"]))
            l_2m = min(float(mn_vals[1]["low"]), float(mn_vals[2]["low"]))
            c_2m = float(mn_vals[1]["close"])
            row_2m = calc_pivots(h_2m, l_2m, c_2m, live_price, 25.0)
            row_2m["tf"] = "2M"
            results.append(row_2m)

    return {"symbol": SYMBOL, "mid": live_price, "timeframes": results}

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>XAUUSD 9-TF Cloud Matrix</title>
<style>
  body { background:#0b0f19; color:#e2e8f0; font-family:system-ui,sans-serif; margin:0; padding:14px; }
  .header { display:flex; justify-content:space-between; align-items:center; background:#111827; padding:14px 18px; border-radius:10px; border:1px solid #1f2937; margin-bottom:14px; }
  .price { font-size:24px; font-weight:700; color:#fbbf24; }
  .wrap { overflow-x:auto; border-radius:10px; }
  table { width:100%; border-collapse:collapse; background:#111827; min-width:680px; }
  th, td { padding:10px 12px; text-align:right; border-bottom:1px solid #1f2937; font-variant-numeric:tabular-nums; font-size:14px; }
  th { background:#1e293b; color:#94a3b8; font-size:11px; text-transform:uppercase; }
  td:first-child, th:first-child { text-align:left; font-weight:700; position:sticky; left:0; background:#111827; }
  td:last-child, th:last-child { text-align:left; }
  .r-col { color:#f87171; } .s-col { color:#34d399; } .eq-col { color:#60a5fa; font-weight:700; }
  .active-cell { background:rgba(251,191,36,0.22); border:1px solid #fbbf24; border-radius:4px; font-weight:700; }
  .badge { padding:4px 8px; border-radius:6px; font-size:11px; font-weight:600; white-space:nowrap; display:inline-block; }
  .badge-active { background:#78350f; color:#fde68a; border:1px solid #f59e0b; }
  .badge-bull { background:#064e3b; color:#6ee7b7; }
  .badge-bear { background:#7f1d1d; color:#fca5a5; }
</style>
</head>
<body>
  <div class="header">
    <div>
      <div style="font-size:11px;color:#94a3b8;">XAU/USD 9-TIMEFRAME CLOUD MATRIX</div>
      <div class="price" id="livePrice">Loading...</div>
    </div>
    <div style="color:#94a3b8;font-size:12px;">15s Refresh</div>
  </div>
  <div class="wrap">
    <table>
      <thead>
        <tr>
          <th>TF</th><th>S3</th><th>S2</th><th>S1</th><th>EQ (P)</th><th>R1</th><th>R2</th><th>R3</th><th>Status</th>
        </tr>
      </thead>
      <tbody id="matrixBody"></tbody>
    </table>
  </div>
<script>
async function refresh() {
  try {
    const res = await fetch('/api/levels');
    const data = await res.json();
    if (data.error) { document.getElementById('livePrice').innerText = data.error; return; }
    document.getElementById('livePrice').innerText = `${data.symbol}: $${data.mid.toFixed(2)}`;
    document.getElementById('matrixBody').innerHTML = data.timeframes.map(row => {
      const L = row.levels;
      const cell = (k, cls) => `<td class="${cls} ${row.is_active && row.closest===k ? 'active-cell' : ''}">${L[k].toFixed(2)}</td>`;
      const bCls = row.is_active ? 'badge-active' : (row.bias==='BULLISH' ? 'badge-bull' : 'badge-bear');
      return `<tr>
        <td>${row.tf}</td>
        ${cell('S3','s-col')}${cell('S2','s-col')}${cell('S1','s-col')}
        ${cell('EQ','eq-col')}
        ${cell('R1','r-col')}${cell('R2','r-col')}${cell('R3','r-col')}
        <td><span class="badge ${bCls}">${row.status}</span></td>
      </tr>`;
    }).join('');
  } catch (e) { console.error(e); }
}
refresh();
setInterval(refresh, 15000);
</script>
</body>
</html>"""
