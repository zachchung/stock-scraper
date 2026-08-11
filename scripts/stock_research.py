import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "stocks.duckdb")

symbol = sys.argv[1].upper() if len(sys.argv) > 1 else "V"

try:
    import duckdb
except ImportError:
    print("duckdb required (see requirements.txt)")
    sys.exit(1)

try:
    con = duckdb.connect(DB, read_only=True)
    use_parquet = False
except Exception:
    con = duckdb.connect()
    use_parquet = True

PARQUET_DIRS = {"ohlcv": "ohlcv_daily"}


def tbl(name, cols, cond=None):
    if use_parquet:
        path = os.path.join(BASE, "data", "stocks", PARQUET_DIRS.get(name, name), "data", f"symbol={symbol}", "*.parquet")
        opts = "union_by_name=true" if name == "ohlcv" else None
        arg = f"'{path}', {opts}" if opts else f"'{path}'"
        return f"SELECT {cols} FROM read_parquet({arg})" + (f" WHERE {cond}" if cond else "")
    return f"SELECT {cols} FROM {name} WHERE symbol='{symbol}'" + (f" AND {cond}" if cond else "")


def q(sql):
    try:
        return con.execute(sql).fetchall()
    except Exception:
        return []


rows = q(f"{tbl('ohlcv', 'date, close')} ORDER BY date DESC LIMIT 1")
if not rows:
    print(f"No price data found for {symbol}")
    sys.exit(1)
price, px_date = rows[0][1], rows[0][0]

fund = q(f"{tbl('fundamentals_snapshot', 'market_cap, fifty_two_week_high, fifty_two_week_low, all_time_high, all_time_low, profit_margin, trailing_pe, forward_pe, return_on_equity, eps_ttm')} ORDER BY fetched_at DESC LIMIT 1")
fund = fund[0] if fund else None

analyst = q(f"{tbl('analyst_targets', 'target_mean, target_high, target_low, recommendation_key, num_analysts')} ORDER BY fetched_at DESC LIMIT 1")
analyst = analyst[0] if analyst else None

annual_cond = "frequency='annual'"
eps_rows = q(f"{tbl('income_statements', 'fiscal_date, diluted_eps, total_revenue', cond=annual_cond)} ORDER BY fiscal_date DESC")

import math

eps_map = {d.year: (d, e) for d, e, _ in eps_rows if e is not None}
if len(eps_map) < 5:
    try:
        import yfinance as yf
        iss = yf.Ticker(symbol).income_stmt
        if "Diluted EPS" in iss.index:
            for col in iss.columns:
                v = iss.loc["Diluted EPS", col]
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    continue
                if v is not None and not math.isnan(v):
                    eps_map.setdefault(col.year, (col, v))
    except Exception:
        pass

print(f"===== {symbol} - Stock Research Snapshot =====")
print(f"As of {px_date}  |  Price ${price:.2f}")
print()

if fund:
    mcap, fhi, flo, ath, alo, margin, tpe, fpe, roe, eps_ttm = fund
    print("Valuation")
    print(f"  Market cap        ${mcap/1e9:,.1f}B")
    if eps_ttm:
        print(f"  TTM P/E           {price/eps_ttm:.1f}x" + (f"   (reported {tpe:.1f}x)" if tpe else ""))
    elif tpe:
        print(f"  TTM P/E           {tpe:.1f}x")
    if fpe:
        print(f"  Forward P/E       {fpe:.1f}x")
    print(f"  Net profit margin {margin*100:.1f}%")
    if roe:
        print(f"  Return on equity  {roe*100:.1f}%")
else:
    print("Valuation: fundamentals_snapshot data unavailable")

print()
if analyst:
    tmean, thigh, tlow, rec, n = analyst
    if tmean:
        print(f"Analyst targets ({n} analysts, {rec})")
        print(f"  Mean target       ${tmean:.2f}   upside {((tmean/price)-1)*100:+.1f}%")
        if thigh and tlow:
            print(f"  High / Low        ${thigh:.2f} / ${tlow:.2f}")
else:
    print("Analyst targets: no data available")

print()
if fund and ath:
    print("Price position")
    print(f"  % down from ATH   {((price/ath)-1)*100:+.1f}%   (ATH ${ath:.2f})")
    if fhi and flo:
        print(f"  52wk high / low   ${fhi:.2f} / ${flo:.2f}")
else:
    print("Price position: all-time-high data unavailable")

years = sorted(eps_map.keys(), reverse=True)
if years:
    print()
    print("Annual EPS (fiscal year)")
    for i, y in enumerate(years[:7]):
        d, e = eps_map[y]
        label = f"FY{d.year % 100:02d} ({d.strftime('%m-%d')} end)"
        if i < len(years) - 1:
            older = eps_map[years[i + 1]][1]
            yoy = (e / older - 1) * 100
            print(f"  {label:<22} ${e:>8.2f}   {yoy:+.1f}%")
        else:
            print(f"  {label:<22} ${e:>8.2f}")
    tail = years[:5]
    if len(tail) >= 3:
        start = eps_map[tail[-1]][1]
        end = eps_map[tail[0]][1]
        cagr = ((end / start) ** (1 / (len(tail) - 1)) - 1) * 100
        print(f"  {'CAGR (last ' + str(len(tail)) + ' yrs)':<22} {cagr:+.1f}%")
else:
    print()
    print("Annual EPS: no annual income statement data available")
