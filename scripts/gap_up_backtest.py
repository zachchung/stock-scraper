import os
import glob
import duckdb
import pandas as pd

GAP_PCT = 0.03
STOP_PCT = 0.05
CAPITAL = 5000.0

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
data_dir = f"{base_dir}/data/stocks/ohlcv_daily/data"
symbol_dirs = sorted(os.path.basename(d) for d in glob.glob(f"{data_dir}/symbol=*"))
symbols = [s.replace("symbol=", "") for s in symbol_dirs]
equities = [s for s in symbols if not s.startswith("%5E") and "%3D" not in s]
NON_SP500 = {"VOO", "BTC-USD", "ETH-USD"}
sp500 = [s for s in equities if s not in NON_SP500]

con = duckdb.connect()

all_rows = []
for sym in symbols:
    path = f"{data_dir}/symbol={sym}/*.parquet"
    try:
        df = con.execute(f"""
            SELECT date, FIRST(open) AS open, FIRST(low) AS low, FIRST(close) AS close
            FROM (SELECT DISTINCT date, open, low, close FROM read_parquet('{path}'))
            GROUP BY date
            ORDER BY date
        """).fetchdf()
    except Exception:
        continue
    if df.empty:
        continue
    df["symbol"] = sym
    all_rows.append(df)
    df = df[["symbol", "date", "open", "close"]]

data = pd.concat(all_rows, ignore_index=True)
con.close()

results = []
symbols_summary = {}

for sym, df in data.groupby("symbol", sort=False):
    df = df.reset_index(drop=True)
    prev_close = df["close"].shift(1)
    gap = df["open"] / prev_close - 1
    signals = df.index[gap > GAP_PCT].tolist()

    trades = {"same_day": [], "d5": [], "d10": []}
    for i in signals:
        entry = df.at[i, "open"]
        if pd.isna(entry) or entry <= 0:
            continue
        stop = entry * (1 - STOP_PCT)
        for label, offset in [("same_day", 0), ("d5", 5), ("d10", 10)]:
            target = i + offset
            if target >= len(df):
                continue
            exit_px = None
            exit_date = None
            for j in range(i, target + 1):
                if j >= len(df):
                    break
                low = df.at[j, "low"]
                if not pd.isna(low) and low <= stop:
                    exit_px = stop
                    exit_date = df.at[j, "date"]
                    break
            if exit_px is None:
                exit_px = df.at[target, "close"]
                exit_date = df.at[target, "date"]
            if pd.isna(exit_px):
                continue
            pnl_pct = exit_px / entry - 1
            trades[label].append({
                "symbol": sym,
                "buy_date": df.at[i, "date"],
                "entry": entry,
                "sell_date": exit_date,
                "exit": exit_px,
                "pnl_pct": pnl_pct,
                "pnl_usd": CAPITAL * pnl_pct,
                "win": exit_px > entry,
            })

    symbols_summary[sym] = {
        "signals": len(signals),
        "trades": {k: len(v) for k, v in trades.items()},
        "trades_list": trades,
    }

print(f"Symbols analyzed: {len(symbols)}")
print(f"Date range: {data['date'].min()} to {data['date'].max()}")
print(f"Entry: open gap-up >{GAP_PCT*100:.0f}% vs prev close | Stop loss: {STOP_PCT*100:.0f}% | ${CAPITAL:,.0f}/trade")
print()

horizon_names = {"same_day": "Same-Day Close", "d5": "Close +5 Days", "d10": "Close +10 Days"}
horizon_keys = ["same_day", "d5", "d10"]

def summarize(trades_by_horizon, label):
    print(f"== {label} ==")
    for key in horizon_keys:
        t = trades_by_horizon[key]
        n = len(t)
        if n == 0:
            print(f"  {horizon_names[key]}: no trades")
            continue
        wins = sum(1 for tr in t if tr["win"])
        win_rate = wins / n * 100
        total_pnl = sum(tr["pnl_usd"] for tr in t)
        avg_pnl = total_pnl / n
        avg_pct = sum(tr["pnl_pct"] for tr in t) / n * 100
        stopped = sum(1 for tr in t if tr["exit"] <= tr["entry"] * (1 - STOP_PCT) + 1e-9)
        print(f"  {horizon_names[key]:<16} trades={n:<6} win_ratio={win_rate:5.1f}%  avg=${avg_pnl:>8,.0f} ({avg_pct:+.2f}%)  total=${total_pnl:>11,.0f}  stopped={stopped}")
    print()

def horizon_trades(rows):
    return {k: [tr for s in rows.values() for tr in s["trades_list"][k]] for k in horizon_keys}

summarize(horizon_trades(symbols_summary), f"ALL {len(symbols)} tickers")

eq_summary = {s: symbols_summary[s] for s in equities if s in symbols_summary}
summarize(horizon_trades(eq_summary), f"EQUITIES ONLY {len(eq_summary)} (excl. indices/futures)")

sp_summary = {s: symbols_summary[s] for s in sp500 if s in symbols_summary}
summarize(horizon_trades(sp_summary), f"S&P 500 ONLY {len(sp_summary)}")

end_date = max(tr["buy_date"] for s in sp_summary.values() for k in horizon_keys for tr in s["trades_list"][k])
print("== S&P 500 ONLY — RECENT WINDOWS ==")
print(f"(reference end date: {end_date}, trades filtered by buy date)")
for months, label in [(3, "Last 3 Months"), (6, "Last 6 Months"), (12, "Last 1 Year")]:
    cutoff = end_date - pd.DateOffset(months=months)
    filtered = {}
    for k in horizon_keys:
        filtered[k] = [tr for s in sp_summary.values() for tr in s["trades_list"][k] if tr["buy_date"] >= cutoff]
    summarize(filtered, label)
    sig = sorted(
        ((sym, sum(1 for k in horizon_keys for tr in sp_summary[sym]["trades_list"][k] if tr["buy_date"] >= cutoff))
         for sym in sp_summary),
        key=lambda x: -x[1])
    sig = [(s, c) for s, c in sig if c > 0]
    if sig:
        print("  Signals by symbol: " + ", ".join(f"{s}={c}" for s, c in sig))
    print()

print("== Per-symbol D10 detail (top 20 by signals) ==")
per_sym = []
for sym in sorted(sp_summary):
    s = sp_summary[sym]
    t = s["trades_list"]["d10"]
    if not t:
        continue
    wins = sum(1 for tr in t if tr["win"])
    per_sym.append((sym, s["signals"], len(t), wins, wins / len(t) * 100, sum(tr["pnl_usd"] for tr in t)))
per_sym.sort(key=lambda x: (-x[1], -x[2]))
print(f"{'Symbol':<8} {'Signals':<8} {'D10 Trades':<11} {'Wins':<6} {'WinRate':<8} {'D10 Total P/L':<15}")
print("-" * 58)
for sym, sig, n, w, wr, pl in per_sym[:20]:
    print(f"{sym:<8} {sig:<8} {n:<11} {w:<6} {wr:<7.1f}% ${pl:<12,.0f}")
