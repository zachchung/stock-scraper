import os
import sys
import duckdb
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "stocks.duckdb")

def usage():
    print("Usage: python drawdown_analysis.py [SYMBOLS] [MIN_DEPTH_PCT] [START]")
    print("  SYMBOLS       comma-separated tickers, e.g. 'v' or 'v,spgi' (default: V)")
    print("  MIN_DEPTH_PCT min drawdown depth in % (default: 10)")
    print("  START         start lookback: years as plain number '5' or a date YYYY-MM-DD (default: 5 years)")
    sys.exit(1)

symbols = sys.argv[1].upper().split(",") if len(sys.argv) > 1 else ["V"]
try:
    min_depth = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
except ValueError:
    usage()
start = sys.argv[3] if len(sys.argv) > 3 else "5"

try:
    con = duckdb.connect(DB, read_only=True)
except Exception:
    con = duckdb.connect()
    PARQUET_DIRS = {"ohlcv": "ohlcv_daily"}

cutoff = None
if "/" not in start and "-" not in start:
    years = float(start)
    last = con.execute("SELECT MAX(date) FROM ohlcv").fetchone()[0]
    cutoff = pd.Timestamp(last) - pd.Timedelta(days=int(years * 365.25))
else:
    cutoff = pd.Timestamp(start)

q = """
WITH r AS (
  SELECT symbol, date, close,
         max(close) OVER (PARTITION BY symbol ORDER BY date) AS running_peak
  FROM ohlcv
  WHERE symbol IN ({syms}) AND date >= DATE '{cut}'
),
seg AS (
  SELECT symbol, date, close, running_peak,
         sum(CASE WHEN close >= running_peak THEN 1 ELSE 0 END)
           OVER (PARTITION BY symbol ORDER BY date ROWS UNBOUNDED PRECEDING) AS seg_id
  FROM r
),
dd AS (
  SELECT symbol, seg_id,
         max(close) AS peak, min(close) AS trough,
         arg_max(date, close) AS peak_date,
         arg_min(date, close) AS trough_date
  FROM seg
  GROUP BY symbol, seg_id
)
SELECT symbol,
       strftime(peak_date, '%Y-%m-%d') AS peak_date,
       strftime(trough_date, '%Y-%m-%d') AS trough_date,
       round(peak, 2) AS peak,
       round(trough, 2) AS trough,
       round(100.0*(peak-trough)/peak, 2) AS dd_pct
FROM dd
WHERE 100.0*(peak-trough)/peak >= {md}
ORDER BY symbol, trough_date
"""
placeholders = ", ".join("?" for _ in symbols)
sql = q.format(syms=placeholders, cut=cutoff.date(), md=min_depth)
rows = con.execute(sql, symbols).fetchall()
con.close()

df = pd.DataFrame(rows, columns=["symbol", "peak_date", "trough_date", "peak", "trough", "dd_pct"])
for sym in symbols:
    sub = df[df.symbol == sym]
    print(f"=== {sym} drawdowns (>={min_depth:.0f}%) since {cutoff.date()} ===")
    if sub.empty:
        print("  none")
    else:
        for _, r in sub.iterrows():
            print(f"  {r['peak_date']} -> {r['trough_date']}   "
                  f"${r['peak']:>9.2f} -> ${r['trough']:>9.2f}   {r['dd_pct']:>6.2f}%")
    print()