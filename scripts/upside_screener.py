import duckdb
import json
import os
import sys

DB = os.path.join(os.path.dirname(__file__), "..", "stocks.duckdb")
MCAP_CACHE = os.path.join(os.path.dirname(__file__), "..", "output", "mcap_cache.json")
PRICE_CACHE = os.path.join(os.path.dirname(__file__), "..", "output", "px_cache.json")
NAME_CACHE = os.path.join(os.path.dirname(__file__), "..", "output", "name_cache.json")
OUT = os.path.join(os.path.dirname(__file__), "..", "output", "sp500_upside.csv")

def load_cache(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def save_cache(path, data):
    with open(path, "w") as f:
        json.dump(data, f)

con = duckdb.connect(DB, read_only=True)
SYMBOLS = [r[0] for r in con.execute("SELECT DISTINCT symbol FROM analyst_targets").fetchall()]

tgt_rows = con.execute(
    """
    SELECT symbol, target_mean, num_analysts, recommendation_key FROM (
        SELECT symbol, target_mean, num_analysts, recommendation_key,
               row_number() OVER (PARTITION BY symbol ORDER BY fetched_at DESC) rn
        FROM analyst_targets
    ) WHERE target_mean IS NOT NULL AND rn = 1
    """
).fetchall()
tgt = {r[0]: r[1:] for r in tgt_rows}

if os.path.exists(MCAP_CACHE):
    with open(MCAP_CACHE) as f:
        mcap = json.load(f)
else:
    import yfinance as yf
    mcap = {}
    for i in range(0, len(SYMBOLS), 25):
        batch = SYMBOLS[i:i + 25]
        tk = yf.Tickers(" ".join(batch))
        for t in tk.tickers.values():
            try:
                mcap[t.ticker] = t.fast_info.market_cap
            except Exception:
                mcap[t.ticker] = None
    with open(MCAP_CACHE, "w") as f:
        json.dump(mcap, f)

if os.path.exists(PRICE_CACHE):
    with open(PRICE_CACHE) as f:
        px = json.load(f)
else:
    import yfinance as yf
    import pandas as pd
    all_syms = SYMBOLS + ["^GSPC"]
    data = yf.download(all_syms, start="2025-12-15", auto_adjust=True, threads=True,
                       progress=False, group_by="ticker")
    px = {}
    for s in all_syms:
        try:
            df = data[s].dropna(subset=["Close"])
            if len(df) == 0:
                continue
            base = df[df.index <= "2025-12-31"]["Close"]
            last = df.iloc[-1]
            px[s] = {
                "base": float(base.iloc[-1]) if len(base) else None,
                "price": float(last["Close"]),
                "date": str(last.name.date()),
            }
        except Exception:
            continue
    with open(PRICE_CACHE, "w") as f:
        json.dump(px, f, default=str)

sp_base = px["^GSPC"]["base"]
sp_price = px["^GSPC"]["price"]
sp500_ytd = (sp_price / sp_base - 1) * 100
print(f"S&P500 YTD {sp500_ytd:.2f}% (base {sp_base:.2f} -> {sp_price:.2f} @ {px['^GSPC']['date']})", file=sys.stderr)

rows = []
for s in SYMBOLS:
    if s not in tgt or s not in px:
        continue
    p = px[s]
    if p["base"] is None or p["price"] is None:
        continue
    tm, num, rating = tgt[s]
    ytd = (p["price"] / p["base"] - 1) * 100
    if ytd >= sp500_ytd:
        continue
    up = (tm / p["price"] - 1) * 100
    if not mcap.get(s) or mcap.get(s) < 100e9:
        continue
    rows.append({
        "Symbol": s, "market_cap": mcap.get(s), "YTD": ytd, "Price": p["price"],
        "Mean Tgt": tm, "Upside%": up, "Analysts": num, "Rating": rating,
    })

rows.sort(key=lambda r: r["Upside%"], reverse=True)

names = load_cache(NAME_CACHE)
fresh = [r["Symbol"] for r in rows if r["Symbol"] not in names]
if fresh:
    import yfinance as yf
    for s in fresh:
        try:
            info = yf.Ticker(s).info
            names[s] = info.get("longName") or info.get("shortName") or s
        except Exception:
            names[s] = s
    save_cache(NAME_CACHE, names)

for r in rows:
    r["Company"] = names.get(r["Symbol"], r["Symbol"])

hdr = ["Symbol", "Company", "market_cap", "YTD", "Price", "Mean Tgt", "Upside%", "Analysts", "Rating"]
print("\t".join(hdr))
with open(OUT, "w") as f:
    f.write(",".join(hdr) + "\n")
    for r in rows:
        mc = f"{r['market_cap'] / 1e9:.1f}B" if r["market_cap"] else "n/a"
        line = [r["Symbol"], r["Company"], mc, f"{r['YTD']:.1f}%", f"{r['Price']:.2f}",
                f"{r['Mean Tgt']:.2f}", f"{r['Upside%']:.1f}%", str(r["Analysts"]), r["Rating"]]
        print("\t".join(line))
        f.write(",".join([r["Symbol"], r["Company"], mc, f"{r['YTD']:.1f}%", f"{r['Price']:.2f}",
                          f"{r['Mean Tgt']:.2f}", f"{r['Upside%']:.1f}%", str(r["Analysts"]),
                          r["Rating"]]) + "\n")