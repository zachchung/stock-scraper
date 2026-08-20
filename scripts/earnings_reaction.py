import os
import sys
import duckdb
import pandas as pd
import yfinance as yf
from datetime import datetime

symbol = sys.argv[1].upper() if len(sys.argv) > 1 else "AAPL"
limit = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 10

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
con = duckdb.connect()

earnings_parquet = f"{base_dir}/data/stocks/earnings_dates/data/symbol={symbol}/*.parquet"
has_local = False
try:
    local_count = con.execute(f"SELECT COUNT(*) FROM read_parquet('{earnings_parquet}')").fetchone()[0]
    if local_count > 0:
        has_local = True
except Exception:
    pass

con.execute("CREATE TEMP TABLE earnings_dates (ed DATE, eps_est DOUBLE, eps_act DOUBLE, surprise DOUBLE, session VARCHAR)")

if has_local:
    con.execute(f"""
        INSERT INTO earnings_dates
        SELECT
            CAST(report_date AS DATE) AS ed,
            eps_estimate,
            eps_actual,
            surprise_pct,
            market_session
        FROM read_parquet('{earnings_parquet}')
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY CAST(report_date AS DATE)
            ORDER BY (eps_actual IS NULL), (eps_estimate IS NULL), report_date DESC
        ) = 1
        ORDER BY report_date DESC
        LIMIT {limit + 5}
    """)
else:
    stock = yf.Ticker(symbol)
    ed = stock.earnings_dates
    if ed is None or ed.empty:
        print(f"No earnings data found for {symbol}")
        sys.exit(1)
    for idx in ed.index[:limit + 5]:
        dt = idx.to_pydatetime()
        eps_est = ed.loc[idx, 'EPS Estimate']
        eps_act = ed.loc[idx, 'Reported EPS']
        surprise = ed.loc[idx, 'Surprise(%)']
        if dt.hour >= 16:
            session = 'post_market'
        elif dt.hour < 9 or (dt.hour == 9 and dt.minute < 30):
            session = 'pre_market'
        else:
            session = 'during_market'
        con.execute(
            "INSERT INTO earnings_dates VALUES (?, ?, ?, ?, ?)",
            [dt.date(),
             None if pd.isna(eps_est) else float(eps_est),
             None if pd.isna(eps_act) else float(eps_act),
             None if pd.isna(surprise) else float(surprise),
             session]
        )

ohlcv_path = f"{base_dir}/data/stocks/ohlcv_daily/data/symbol={symbol}/*.parquet"
has_ohlcv = os.path.isdir(f"{base_dir}/data/stocks/ohlcv_daily/data/symbol={symbol}")
if not has_ohlcv:
    print(f"No OHLCV data found for {symbol}")
    con.execute("DROP TABLE earnings_dates")
    sys.exit(1)

ohlcv_cte = """
WITH ohlcv_raw AS (
    SELECT DISTINCT date, open, close FROM read_parquet('""" + ohlcv_path + """')
),
ohlcv AS (
    SELECT date, FIRST(open) AS open, FIRST(close) AS close
    FROM ohlcv_raw GROUP BY date
),
ohlcv_rn AS (
    SELECT date, open, close, ROW_NUMBER() OVER (ORDER BY date) AS rn
    FROM ohlcv
)
"""

post_market_query = ohlcv_cte + """
SELECT
    e.ed AS earning_date,
    e.session,
    (SELECT date FROM ohlcv_rn WHERE rn = (SELECT MIN(rn) FROM ohlcv_rn WHERE date > e.ed)) AS reaction_date,
    e.eps_est,
    e.eps_act,
    e.surprise,
    ROUND(
        ((SELECT open FROM ohlcv_rn WHERE rn = (SELECT MIN(rn) FROM ohlcv_rn WHERE date > e.ed)) -
         (SELECT close FROM ohlcv_rn WHERE rn = (SELECT MAX(rn) FROM ohlcv_rn WHERE date <= e.ed))) /
         (SELECT close FROM ohlcv_rn WHERE rn = (SELECT MAX(rn) FROM ohlcv_rn WHERE date <= e.ed)) * 100, 2) AS day_open_pct,
    ROUND(
        ((SELECT close FROM ohlcv_rn WHERE rn = (SELECT MIN(rn) FROM ohlcv_rn WHERE date > e.ed)) -
         (SELECT close FROM ohlcv_rn WHERE rn = (SELECT MAX(rn) FROM ohlcv_rn WHERE date <= e.ed))) /
         (SELECT close FROM ohlcv_rn WHERE rn = (SELECT MAX(rn) FROM ohlcv_rn WHERE date <= e.ed)) * 100, 2) AS day_close_pct,
    ROUND(
        ((SELECT close FROM ohlcv_rn WHERE rn = (SELECT MIN(rn) FROM ohlcv_rn WHERE date > e.ed) + 5) -
         (SELECT close FROM ohlcv_rn WHERE rn = (SELECT MAX(rn) FROM ohlcv_rn WHERE date <= e.ed))) /
         (SELECT close FROM ohlcv_rn WHERE rn = (SELECT MAX(rn) FROM ohlcv_rn WHERE date <= e.ed)) * 100, 2) AS day5_close_pct
FROM earnings_dates e
WHERE (SELECT date FROM ohlcv_rn WHERE rn = (SELECT MIN(rn) FROM ohlcv_rn WHERE date > e.ed)) IS NOT NULL
  AND (SELECT close FROM ohlcv_rn WHERE rn = (SELECT MIN(rn) FROM ohlcv_rn WHERE date > e.ed) + 5) IS NOT NULL
ORDER BY e.ed DESC
LIMIT """ + str(limit)

pre_market_query = ohlcv_cte + """
SELECT
    e.ed AS earning_date,
    e.session,
    e.ed AS reaction_date,
    e.eps_est,
    e.eps_act,
    e.surprise,
    ROUND(
        ((SELECT open FROM ohlcv_rn WHERE date = e.ed) -
         (SELECT close FROM ohlcv_rn WHERE rn = (SELECT MAX(rn) FROM ohlcv_rn WHERE date < e.ed))) /
         (SELECT close FROM ohlcv_rn WHERE rn = (SELECT MAX(rn) FROM ohlcv_rn WHERE date < e.ed)) * 100, 2) AS day_open_pct,
    ROUND(
        ((SELECT close FROM ohlcv_rn WHERE date = e.ed) -
         (SELECT close FROM ohlcv_rn WHERE rn = (SELECT MAX(rn) FROM ohlcv_rn WHERE date < e.ed))) /
         (SELECT close FROM ohlcv_rn WHERE rn = (SELECT MAX(rn) FROM ohlcv_rn WHERE date < e.ed)) * 100, 2) AS day_close_pct,
    ROUND(
        ((SELECT close FROM ohlcv_rn WHERE rn = (SELECT rn FROM ohlcv_rn WHERE date = e.ed) + 5) -
         (SELECT close FROM ohlcv_rn WHERE rn = (SELECT MAX(rn) FROM ohlcv_rn WHERE date < e.ed))) /
         (SELECT close FROM ohlcv_rn WHERE rn = (SELECT MAX(rn) FROM ohlcv_rn WHERE date < e.ed)) * 100, 2) AS day5_close_pct
FROM earnings_dates e
WHERE (SELECT date FROM ohlcv_rn WHERE date = e.ed) IS NOT NULL
  AND (SELECT close FROM ohlcv_rn WHERE rn = (SELECT rn FROM ohlcv_rn WHERE date = e.ed) + 5) IS NOT NULL
ORDER BY e.ed DESC
LIMIT """ + str(limit)

# Determine if any recent earnings are pre_market vs post_market
sample = con.execute("SELECT DISTINCT session FROM earnings_dates").fetchall()
sessions = [r[0] for r in sample]
session_set = set(sessions)
is_pre = session_set == {'pre_market'}
is_post = session_set.issubset({'post_market', 'during_market'})

if is_pre:
    query = pre_market_query
elif is_post:
    query = post_market_query
else:
    # Mixed: detect per-row
    query = ohlcv_cte + """
    , base AS (
        SELECT
            e.ed,
            e.session,
            e.eps_est,
            e.eps_act,
            e.surprise,
            -- same-day references (pre_market / during_market)
            (SELECT open FROM ohlcv_rn WHERE date = e.ed) AS same_open,
            (SELECT close FROM ohlcv_rn WHERE date = e.ed) AS same_close,
            (SELECT close FROM ohlcv_rn WHERE rn = (SELECT MAX(rn) FROM ohlcv_rn WHERE date < e.ed)) AS prev_close,
            (SELECT rn FROM ohlcv_rn WHERE date = e.ed) AS same_rn,
            -- next-day references (post_market)
            (SELECT open FROM ohlcv_rn WHERE rn = (SELECT MIN(rn) FROM ohlcv_rn WHERE date > e.ed)) AS next_open,
            (SELECT close FROM ohlcv_rn WHERE rn = (SELECT MIN(rn) FROM ohlcv_rn WHERE date > e.ed)) AS next_close,
            (SELECT close FROM ohlcv_rn WHERE rn = (SELECT MAX(rn) FROM ohlcv_rn WHERE date <= e.ed)) AS prev_close_incl,
            (SELECT MIN(rn) FROM ohlcv_rn WHERE date > e.ed) AS next_rn,
            (SELECT date FROM ohlcv_rn WHERE date = e.ed) AS has_same_day
        FROM earnings_dates e
    )
    SELECT
        ed AS earning_date,
        session,
        CASE WHEN session = 'pre_market' AND has_same_day IS NOT NULL THEN ed
             ELSE (SELECT MIN(date) FROM ohlcv_rn WHERE date > ed)
        END AS reaction_date,
        eps_est, eps_act, surprise,
        ROUND(
            CASE WHEN session = 'pre_market'
                 THEN (same_open - prev_close) / prev_close * 100
                 ELSE (next_open - prev_close_incl) / prev_close_incl * 100
            END, 2
        ) AS day_open_pct,
        ROUND(
            CASE WHEN session = 'pre_market'
                 THEN (same_close - prev_close) / prev_close * 100
                 ELSE (next_close - prev_close_incl) / prev_close_incl * 100
            END, 2
        ) AS day_close_pct,
        ROUND(
            CASE WHEN session = 'pre_market'
                 THEN ((SELECT close FROM ohlcv_rn WHERE rn = same_rn + 5) - prev_close) / prev_close * 100
                 ELSE ((SELECT close FROM ohlcv_rn WHERE rn = next_rn + 5) - prev_close_incl) / prev_close_incl * 100
            END, 2
        ) AS day5_close_pct
    FROM base
    WHERE (CASE WHEN session = 'pre_market' AND has_same_day IS NOT NULL THEN has_same_day
                ELSE (SELECT MIN(date) FROM ohlcv_rn WHERE date > ed) END) IS NOT NULL
      AND (CASE WHEN session = 'pre_market'
                THEN ((SELECT close FROM ohlcv_rn WHERE rn = same_rn + 5) - prev_close) / prev_close * 100
                ELSE ((SELECT close FROM ohlcv_rn WHERE rn = next_rn + 5) - prev_close_incl) / prev_close_incl * 100
           END) IS NOT NULL
    ORDER BY ed DESC
    LIMIT """ + str(limit)

res = con.execute(query).fetchall()

header = f"{'EarningDate':<14} {'Release':<13} {'ReactionDate':<14} {'EPS Est':<8} {'EPS Act':<8} {'Surp%':<7} {'DayOpen%':<9} {'DayClose%':<9} {'5DayClose%':<9}"
print(f"\nPast {len(res)} post-earnings reactions for {symbol}")
print("=" * len(header))
print(header)
print("-" * 91)

pos = neg = 0
max_up, max_down = -999.99, 999.99
for r in res:
    do_pct = r[6] if r[6] is not None else 0.0
    dc_pct = r[7] if r[7] is not None else 0.0
    if dc_pct > 0: pos += 1
    else: neg += 1
    max_up = max(max_up, dc_pct)
    max_down = min(max_down, dc_pct)
    est = f'{r[3]:.2f}' if r[3] else 'N/A'
    act = f'{r[4]:.2f}' if r[4] else 'N/A'
    surp = f'{r[5]:+.2f}' if r[5] else 'N/A'
    d5 = f'{r[8]:+.2f}%' if r[8] is not None else 'N/A'
    ed_dt = datetime.strptime(str(r[0]), '%Y-%m-%d')
    rd_dt = datetime.strptime(str(r[2]), '%Y-%m-%d')
    do_str = f"{do_pct:+.2f}%"
    dc_str = f"{dc_pct:+.2f}%"
    print(f"{ed_dt.strftime('%m/%d/%Y'):<14} {r[1]:<13} {rd_dt.strftime('%m/%d/%Y'):<14} {est:<8} {act:<8} {surp:<7} {do_str:<9} {dc_str:<9} {d5:<9}")

print("-" * 91)
print(f"Positive reactions: {pos}  |  Negative reactions: {neg}")
print(f"Max up move: {max_up:+.2f}%  |  Max down move: {max_down:+.2f}%")

con.execute("DROP TABLE earnings_dates")
