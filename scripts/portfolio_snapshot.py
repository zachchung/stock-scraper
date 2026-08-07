#!/usr/bin/env python3
"""
Portfolio snapshot: holdings + PnL on any date, with automatic split reconciliation.

Data sources
  - Splits .......... local corporate_actions table (populated by scraper.py
                      --corporate-actions; authoritative ratios + effective dates)
  - Dividends ....... local corporate_actions table (per-share on ex-date),
                      folded into realized P&L as income
  - Prices .......... local stock_scraper OHLCV (already split-adjusted), yfinance fallback
  - Transactions .... your CSV file (see format below)

Both splits and dividends fall back to a live yfinance fetch when the local
table has no rows for a ticker (e.g. not yet ingested).

Split reconciliation
  Every transaction is normalized to a single canonical basis: CURRENT post-split
  shares. For a transaction on date D, the cumulative split factor F equals the
  product of all split ratios occurring strictly after D. Dollar cost basis is
  invariant across splits (shares*price), so only the share count is rescaled.

  For each row we decide whether it was recorded already-adjusted or raw by
  comparing its price to the local split-adjusted price on that date:
      raw       price ~= adj_price * F   -> shares *= F
      adjusted  price ~= adj_price       -> shares unchanged
      neither   -> FLAGGED for manual review

CSV format (headers; side must be BUY or SELL):
  date,ticker,side,shares,price
  2021-11-01,GOOGL,BUY,1,1000.00

Usage:
  .venv/bin/python scripts/portfolio_snapshot.py --input txs.csv --date 2024-06-30
"""

import argparse
import functools
import glob
import os
import sys

import duckdb
import pandas as pd
import yfinance as yf

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CON = duckdb.connect()

# Local corporate-actions warehouse (splits + dividends), populated by
# `scraper.py --corporate-actions`. Read via DuckDB iceberg_scan (ACTIVE snapshot
# only), NOT raw parquet globs: every MERGE/upsert rewrites rows into new data
# files while old files linger until snapshots are expired, so raw globs
# double/triple-count rows. iceberg_scan reads only the current snapshot.
CORP_ACTIONS_TABLE = f"{BASE_DIR}/data/stocks/corporate_actions"

# Withholding tax on dividends, applied to the "incl div" P&L figures.
DIV_TAX_RATE = 0.30

# Ticker alias map: some exports use a legacy symbol for a company whose ticker
# has since changed. Every alias is normalized to its canonical symbol BEFORE any
# processing so the position, prices and corporate actions merge across the
# rename (a ticker change is NOT a split -- shares carry over 1:1, no rescale).
#
# SQ -> XYZ: Block, Inc. traded as SQ until it adopted the XYZ ticker in
# January 2024, so any pre-2024 SQ position is an XYZ position. SQ is also
# delisted on yfinance (no timezone; the fallback fetch hangs), so keeping it
# under the legacy symbol breaks both pricing and corporate-action lookups.
TICKER_ALIASES = {
    "SQ": "XYZ",
}


OUT_DIR = "/Users/ZacharyChung1/code/stock_scraper/output"


def latest_transactions():
    """Return the path of the most recent transactions_*.csv in the output dir."""
    pattern = os.path.join(OUT_DIR, "transactions_*.csv")
    files = sorted(glob.glob(pattern))
    if not files:
        sys.exit(f"No transactions file found in {OUT_DIR} (expected transactions_YYYY-MM-DD.csv). "
                 "Pass --input explicitly or create one via parse_pasted_txs.py.")
    return files[-1]


def load_transactions(path):
    df = pd.read_csv(path)
    need = {"date", "ticker", "side", "shares", "price"}
    missing = need - set(df.columns.str.lower())
    if missing:
        sys.exit(f"Missing columns: {sorted(missing)} (expected {sorted(need)})")
    df.columns = [c.lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"])
    df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()
    df["ticker"] = df["ticker"].replace(TICKER_ALIASES)
    df["side"] = df["side"].astype(str).str.upper().str.strip()
    df["shares"] = df["shares"].astype(float)
    df["price"] = df["price"].astype(float)
    bad = ~df["side"].isin({"BUY", "SELL"})
    if bad.any():
        sys.exit(f"Invalid side values: {df.loc[bad, 'side'].unique().tolist()}")
    return df.sort_values("date", kind="stable").reset_index(drop=True)


@functools.lru_cache(maxsize=None)
def get_splits(ticker):
    """DataFrame [date, factor]; factor = new shares per 1 old share.

    Reads split history from the LOCAL corporate_actions warehouse (populated
    by `scraper.py --corporate-actions`) via DuckDB. Falls back to a live
    yfinance fetch only if the local table has no split rows for the ticker.
    """
    try:
        df = CON.execute(
            "SELECT date, split_factor AS factor FROM iceberg_scan(?) "
            "WHERE action_type = 'split' AND symbol = ? ORDER BY date",
            [CORP_ACTIONS_TABLE, ticker],
        ).fetchdf()
        if len(df):
            return df
    except Exception:
        pass
    raw = yf.Ticker(ticker).splits  # Series: DatetimeIndex -> ratio
    recs = []
    if raw is not None and len(raw):
        for idx, ratio in raw.items():
            recs.append({"date": idx.date(), "factor": float(ratio)})
    df = pd.DataFrame(recs)
    if len(df):
        df["date"] = pd.to_datetime(df["date"])
    return df


@functools.lru_cache(maxsize=None)
def get_dividends(ticker):
    """DataFrame [date, amount]; per-share dividend on each ex-date.

    Reads dividend history from the LOCAL corporate_actions warehouse when
    available; falls back to a live yfinance fetch otherwise (same policy as
    get_splits, so tickers not yet ingested still report correctly).
    """
    try:
        df = CON.execute(
            "SELECT date, amount FROM iceberg_scan(?) "
            "WHERE action_type = 'dividend' AND symbol = ? ORDER BY date",
            [CORP_ACTIONS_TABLE, ticker],
        ).fetchdf()
        if len(df):
            return df
    except Exception:
        pass
    raw = yf.Ticker(ticker).dividends  # Series: DatetimeIndex -> per-share amount
    recs = []
    if raw is not None and len(raw):
        for idx, amount in raw.items():
            recs.append({"date": idx.date(), "amount": float(amount)})
    df = pd.DataFrame(recs)
    if len(df):
        df["date"] = pd.to_datetime(df["date"])
    return df


def cum_factor(ticker, date):
    """Product of split ratios occurring strictly after `date`."""
    s = get_splits(ticker)
    if len(s) == 0:
        return 1.0
    f = 1.0
    for _, r in s.iterrows():
        if r["date"].date() > date.date():
            f *= r["factor"]
    return f


# Yahoo/local OHLCV stores GBTC in spin-off-adjusted (NAV-based) terms. To get
# the actual unadjusted market price as traded on the OTCQX, scale it up.
GBTC_unadjusted_FACTOR = 1.12138


def local_price_factor(ticker):
    """Multiplier applied to local (yfinance) OHLCV to restore the real traded
    (unadjusted) price. Only GBTC differs (spin-off adjustment)."""
    return GBTC_unadjusted_FACTOR if ticker == "GBTC" else 1.0


def local_price_at(ticker, date):
    """Split-adjusted close (nondent) nearest <= date, or None."""
    p = f"{BASE_DIR}/data/stocks/ohlcv_daily/data/symbol={ticker}/*.parquet"
    if not glob.glob(p):
        return None
    try:
        row = CON.execute(
            "SELECT close FROM read_parquet(?) WHERE date <= ? ORDER BY date DESC LIMIT 1",
            [p, date.strftime("%Y-%m-%d")],
        ).fetchone()
        return None if row is None else row[0] * local_price_factor(ticker)
    except Exception:
        return None


@functools.lru_cache(maxsize=None)
def split_factors_after(ticker, date):
    """Sorted set of all subset-product "levels" M such that recorded price ~=
    adjusted * M, where M is the product of an arbitrary subset of the split
    ratios occurring strictly after `date`.

    A transaction may be recorded at any point on the split timeline: fully raw
    (M = F = product of ALL post-trade splits), fully adjusted (M = 1), or an
    intermediate level where only some splits have been applied (e.g. post-4:1
    but pre-10:1 => M = 4). canon_shares = recorded_shares * F / M gives the
    clean whole share count for the matched level."""
    s = get_splits(ticker)
    if len(s) == 0:
        return (1.0,)
    factors = tuple(float(r["factor"]) for _, r in s.iterrows()
                    if r["date"].date() > date.date())
    if not factors:
        return (1.0,)
    levels = {1.0}
    for f in factors:
        levels = levels | {v * f for v in levels}
    return tuple(sorted(levels, reverse=True))


def local_ohlc_at(ticker, date):
    """Split-adjusted (high, low, close) for the session nearest <= date, or None."""
    p = f"{BASE_DIR}/data/stocks/ohlcv_daily/data/symbol={ticker}/*.parquet"
    if not glob.glob(p):
        return None
    try:
        row = CON.execute(
            "SELECT high, low, close FROM read_parquet(?) WHERE date <= ? ORDER BY date DESC LIMIT 1",
            [p, date.strftime("%Y-%m-%d")],
        ).fetchone()
        if row is None:
            return None
        f = local_price_factor(ticker)
        return (row[0] * f, row[1] * f, row[2] * f)  # (high, low, close)
    except Exception:
        return None


def _in_range(price, lo, hi, tol):
    """True if price falls within the daily [lo, hi] range, or within `tol`
    (fraction of the nearest range bound) outside it, to allow genuine
    pre-/post-market fills below/above the regular-session range. Prices far
    from the session (e.g. data errors) must NOT match."""
    if lo is None or hi is None or hi <= 0:
        return False
    if lo <= price <= hi:
        return True
    if price < lo:
        return (lo - price) / lo <= tol
    return (price - hi) / hi <= tol


def normalize(df, tolerance):
    """Normalize every transaction to CURRENT post-split share count.

    Cost basis (shares * price) is invariant under splits, so we never trust
    the recorded share count alone. Instead we reconcile against the local
    split-adjusted HIGH/LOW/CLOSE on the trade date:

      - If recorded price falls within (or just outside, pre-/post-market) the
        raw (pre-split) daily range -> shares *= F.
      - If it falls within the adjusted daily range -> shares are already current.
      - Otherwise the row can't be classified cleanly: we do NOT fabricate a
        share count from the price ratio. We keep the split-scaled recorded
        shares (the neutral fallback, same as the no-local-price case) and flag
        the row loudly so it is surfaced for manual verification.
      - If no local price, fall back to shares * F.

    Using the daily range (not just the close) lets valid fill prices that land
    between low/high or a few percent outside the session (pre/post-market) be
    reconciled cleanly. Rows that still can't be classified are kept with the
    split-scaled shares and FLAGGED for manual review -- main() prints a banner
    and exits non-zero so the user is notified instead of trusting a guess.
    Returns a dict of DataFrames: the reconciled transactions."""
    out = df.copy()
    out["canon_shares"] = 0.0
    out["cost_basis"] = 0.0
    out["basis"] = ""
    out["flag_reason"] = ""
    for i in out.index:
        row = out.loc[i]
        ticker, d = row["ticker"], row["date"]
        F = cum_factor(ticker, d)
        ohlc = local_ohlc_at(ticker, d)
        cost = row["shares"] * row["price"]

        if ohlc is None:
            out.at[i, "canon_shares"] = row["shares"] * F
            out.at[i, "flag_reason"] = "no local price; scaled by split factor" if F > 1.0001 else ""
            out.at[i, "basis"] = "fallback*F"
            out.at[i, "cost_basis"] = cost
            continue

        hi, lo, adj = ohlc  # split-adjusted high / low / close
        p = row["price"]
        # Levels M = product of an arbitrary subset of the post-trade splits:
        # M=1 -> already fully adjusted, M=F -> fully raw, in-between -> some
        # splits already applied (e.g. M=4 for post-4:1/pre-10:1). For the
        # matched level, canon = recorded_shares * M, which is a clean whole
        # count when M is an integer subset product (no fuzzy /adjclose).
        matched_level = None
        for M in split_factors_after(ticker, d):
            if _in_range(p, lo * M, hi * M, tolerance):
                matched_level = M
                break

        if matched_level is None:
            # No split level matches (e.g. bad recorded price/date). Use the
            # split-invariant dollar/adjclose estimate as a last resort: cost
            # basis is invariant across splits, so current shares = dollars /
            # adjusted close is correct at ANY recording basis. Flag loudly.
            out.at[i, "canon_shares"] = cost / adj
            out.at[i, "basis"] = "est(dollar/adjclose)"
            out.at[i, "flag_reason"] = (
                f"price {p:.2f} outside all split levels "
                f"[{min(lo*M for M in split_factors_after(ticker,d)):.2f}, "
                f"{max(hi*M for M in split_factors_after(ticker,d)):.2f}] "
                f"(tol={tolerance:.0%}); estimated dollar/adjclose -- VERIFY manually"
            )
        else:
            out.at[i, "canon_shares"] = row["shares"] * matched_level
            if matched_level == 1:
                out.at[i, "basis"] = "adjusted"
            elif abs(matched_level - F) < 1e-9:
                out.at[i, "basis"] = "raw"
            else:
                out.at[i, "basis"] = f"level {matched_level:g}"
        out.at[i, "cost_basis"] = cost
    return out


def dedupe(df):
    """Collapse rows that are the SAME economic event recorded in two split
    bases on the same ticker+date (e.g. '1 @ 2869' raw and '20 @ 143.4'
    adjusted). These have an equal dollar cost BUT very different share counts
    (differing by the split factor). Only this signature is collapsed; equal-
    share, near-identical-priced same-day transactions are genuine and kept."""
    out = df.copy()
    out["skip"] = False
    out["dup_of"] = None
    for (tkr, d), g in out.groupby(["ticker", out["date"].dt.date]):
        g = g.sort_values("shares")
        seen = []
        for idx, row in g.iterrows():
            val = row["shares"] * row["price"]
            match = False
            for sv, sv_shares in seen:
                same_dollar = abs(val - sv) / max(sv, 1e-9) < 0.001
                # share counts must be VERY different (split-scale), not ~equal
                ratio = max(row["shares"], sv_shares) / max(min(row["shares"], sv_shares), 1e-9)
                split_diff = ratio >= 1.5
                if same_dollar and split_diff:
                    out.at[idx, "skip"] = True
                    out.at[idx, "dup_of"] = f"${sv:,.2f} on {d}"
                    match = True
                    break
            if not match:
                seen.append((val, row["shares"]))
    return out[~out["skip"]].copy(), out[out["skip"]].copy()


def build_snapshot(reconciled, date, method="fifo"):
    txs = reconciled[reconciled["date"] <= pd.Timestamp(date)].copy()
    realized = {}
    open_pos = {}
    over_sell = []

    for _, t in txs.iterrows():
        tkr, canon = t["ticker"], t["canon_shares"]
        cost_per = t["cost_basis"] / canon if canon else 0.0
        lot = {"shares": canon, "cost": cost_per}
        if t["side"] == "BUY":
            open_pos.setdefault(tkr, []).append(lot)
        else:
            rem = canon
            sold_cost = 0.0
            lots = open_pos.get(tkr, [])
            while rem > 1e-9 and lots:
                idx = 0 if method.lower() == "fifo" else len(lots) - 1
                head = lots[idx]
                take = min(rem, head["shares"])
                sold_cost += take * head["cost"]
                head["shares"] -= take
                rem -= take
                if head["shares"] <= 1e-9:
                    lots.pop(idx)
            proceeds = t["cost_basis"]  # true dollar proceeds (split-invariant)
            if sold_cost > 0:
                realized[tkr] = realized.get(tkr, 0.0) + (proceeds - sold_cost)
            if rem > 1e-9:
                over_sell.append((tkr, t["date"], t["shares"], rem))
            realized.setdefault(tkr, 0.0)

    holdings = []
    for tkr, lots_open in open_pos.items():
        total_sh = sum(lo["shares"] for lo in lots_open)
        total_cost = sum(lo["shares"] * lo["cost"] for lo in lots_open)
        price = local_price_at(tkr, date)
        market = total_sh * price if price is not None else None
        holdings.append({
            "ticker": tkr,
            "shares": total_sh,
            "cost_basis": total_cost,
            "market_value": market,
            "unrealized": market - total_cost if market is not None else None,
        })
    holdings.sort(key=lambda h: h["ticker"])

    # Dividend income: cash received on each ex-date = shares held at that date
    # (current post-split terms) x per-share amount. The amount from
    # corporate_actions/yfinance is ALREADY split-adjusted (per current share),
    # so post-split shares x amount = actual dollars received — no split scaling.
    # Folding into `realized` makes it flow through every report; `dividends`
    # is returned separately for transparency.
    dividends = {}
    div_scope = set(open_pos) | set(realized)
    for tkr in div_scope:
        divs = get_dividends(tkr)
        if len(divs) == 0:
            continue
        txs_tkr = txs[txs["ticker"] == tkr].sort_values("date")
        tx_it = txs_tkr.iterrows()
        tx = next(tx_it, None)
        shares = income = 0.0
        for _, d in divs.iterrows():
            if d["date"].date() > date.date():
                continue
            while tx is not None and tx[1]["date"] < d["date"]:
                shares += tx[1]["canon_shares"] if tx[1]["side"] == "BUY" else -tx[1]["canon_shares"]
                tx = next(tx_it, None)
            if shares > 1e-9:
                income += shares * d["amount"]
        if income:
            dividends[tkr] = income
            realized[tkr] = realized.get(tkr, 0.0) + income

    return holdings, realized, over_sell, dividends


def monthly_report(df, rec, tolerance, max_date, method="fifo"):
    """Print holdings + PnL at the end of every month from first trade to max_date."""
    first = rec["date"].min().to_period("M")
    last = pd.Timestamp(max_date).to_period("M")
    print(f"\nMonthly snapshots ({first} -> {last}) [{method.upper()}]")
    print("=" * 88)
    print(f"{'Month End':<12}{'Shares':>9}{'Cost $':>13}{'Market Value':>13}"
          f"{'Unreal. $':>12}{'Real.+Div $':>12}{'Net P&L $':>12}")
    print("-" * 88)
    period = first
    while period <= last:
        month_end = period.to_timestamp("M")
        holdings, realized, _, _ = build_snapshot(rec, month_end, method)
        shares = sum(h["shares"] for h in holdings)
        cost = sum(h["cost_basis"] for h in holdings)
        val = sum(h["market_value"] for h in holdings if h["market_value"] is not None)
        unreal = val - cost if cost else 0.0
        real = sum(realized.values())
        if shares != 0 or real != 0:
            print(f"{str(month_end.date()):<12}{shares:>9,.4f}{cost:>13,.2f}{val:>13,.2f}"
                  f"{unreal:>12,.2f}{real:>12,.2f}{unreal + real:>12,.2f}")
        period = period + 1


def monthly_breakdown(rec, max_date, method="fifo"):
    """Per-ticker holdings + PnL table for EVERY month-end, from first trade to max_date."""
    first = rec["date"].min().to_period("M")
    last = pd.Timestamp(max_date).to_period("M")
    period = first
    while period <= last:
        month_end = period.to_timestamp("M")
        holdings, realized, _, _ = build_snapshot(rec, month_end, method)
        if not holdings and not any(realized.values()):
            period = period + 1
            continue
        print(f"\n{'=' * 96}")
        print(f"Month: {month_end.strftime('%Y-%m-%d')}  [{method.upper()}]")
        print("=" * 96)
        print(f"{'Ticker':<9}{'Shares':>11}{'Cost $':>13}{'Market Value':>13}"
              f"{'Unrealized $':>13}{'Real.+Div $':>12}{'Total P&L $':>13}")
        print("-" * 96)
        tot_cost = tot_val = tot_unreal = 0.0
        for h in holdings:
            val = h["market_value"] if h["market_value"] is not None else 0.0
            unreal = h["unrealized"] if h["unrealized"] is not None else 0.0
            real = realized.get(h["ticker"], 0.0)
            if h["unrealized"] is not None:
                tot_cost += h["cost_basis"]
                tot_val += val
            tot_unreal += unreal
            total = unreal + real
            print(f"{h['ticker']:<9}{h['shares']:>11,.4f}{h['cost_basis']:>13,.2f}"
                  f"{val:>13,.2f}{unreal:>13,.2f}{real:>12,.2f}{total:>13,.2f}")
        real_total = sum(realized.values())
        print("-" * 96)
        print(f"{'TOTAL':<9}{'':>11}{tot_cost:>13,.2f}{tot_val:>13,.2f}"
              f"{tot_unreal:>13,.2f}{real_total:>12,.2f}{tot_unreal + real_total:>13,.2f}")
        period = period + 1


def trade_calendar():
    """Sorted set of US-trading dates (as datetime.date) from the S&P 500 index parquet."""
    p = f"{BASE_DIR}/data/stocks/ohlcv_daily/data/symbol=*GSPC/*.parquet"
    if not glob.glob(p):
        return None
    try:
        rows = CON.execute("SELECT DISTINCT date FROM read_parquet(?)", [p]).fetchall()
        return {pd.to_datetime(x[0]).date() for x in rows}
    except Exception:
        return None


def next_trading_day(cal, day):
    """First trading date at or after `day` (day is a date/time)."""
    d = pd.Timestamp(day).date()
    for _ in range(20):
        if cal is None or d in cal:
            return pd.Timestamp(d)
        d += pd.Timedelta(days=1)
    return pd.Timestamp(day)


def prev_trading_day(cal, day):
    """Last trading date at or before `day`."""
    d = pd.Timestamp(day).date()
    for _ in range(40):
        if cal is None or d in cal:
            return pd.Timestamp(d)
        d -= pd.Timedelta(days=1)
    return pd.Timestamp(day)


def series_dates(rec, last, schedule="month-end", dom=None, start=None):
    """Snapshot dates for the portfolio series from `start` (first trade if None)
    to `last`.

    schedule = 'month-end' : last trading day of each month.
    schedule = 'mdom'      : the `dom`-th calendar day of each month, snapped
                             forward to the next trading day if it's a holiday/weekend.
    """
    cal = trade_calendar()
    first = rec["date"].min()
    seq_start = pd.Timestamp(start) if start else first
    dates = []
    period = seq_start.to_period("M")
    last_period = pd.Timestamp(last).to_period("M")
    while period <= last_period:
        if schedule == "month-end":
            day = prev_trading_day(cal, period.to_timestamp("M"))
            # Skip months with no trading data (resolved day fell outside the month)
            if day is not None and day.to_period("M") != period:
                period = period + 1
                continue
        else:
            try:
                day = pd.Timestamp(period.year, period.month, dom)
            except ValueError:
                day = None
            day = None if day is None else next_trading_day(cal, day)
        if day is not None and pd.Timestamp(first) <= day <= pd.Timestamp(last):
            dates.append(day)
        period = period + 1
    return dates


def portfolio_series(rec, dates, method="fifo", div_tax_rate=DIV_TAX_RATE):
    """Per-date portfolio TOTALS (no per-ticker breakdown). Each row aggregates
    all holdings the way the snapshot TOTAL row does.

    Returns list of dicts: date, shares, cost, value, unrealized, realized,
    total_pnl, avg_net_cost, dividends (pre-tax), total_pnl_excl_div,
    total_pnl_incl_div. `total_pnl` = realized + unrealized (realized already
    includes pre-tax dividends, as in build_snapshot); Total P&L (excl div) =
    total_pnl − pre-tax dividends; Total P&L (incl div) = total_pnl −
    div_tax_rate × pre-tax dividends. Avg Net Cost = (Cost − Realized) /
    Shares, blended across the whole portfolio.
    """
    rows = []
    for d in dates:
        holdings, realized, _, dividends = build_snapshot(rec, d, method)
        shares = sum(h["shares"] for h in holdings)
        cost = sum(h["cost_basis"] for h in holdings)
        val = sum(h["market_value"] for h in holdings if h["market_value"] is not None)
        unreal = val - cost if cost else 0.0
        real = sum(realized.values())
        total = unreal + real
        div_pre = sum(dividends.values())
        div_post = div_pre * (1 - div_tax_rate)
        total_excl = total - div_pre
        total_incl = total - div_tax_rate * div_pre
        avg_net = (cost - real) / shares if shares else 0.0
        rows.append({
            "date": pd.Timestamp(d),
            "shares": shares,
            "cost": cost,
            "value": val,
            "unrealized": unreal,
            "realized": real,
            "total_pnl": total,
            "avg_net_cost": avg_net,
            "dividend_pre": div_pre,
            "dividend_post": div_post,
            "total_pnl_excl_div": total_excl,
            "total_pnl_incl_div": total_incl,
        })
    return rows


def dividend_breakdown(rec, date, div_tax_rate=DIV_TAX_RATE):
    """Per-ex-date dividends earned while holding, one row per symbol/date.

    Shares held = post-split (canonical) share count at that ex-date; the
    per-share amount is split-adjusted, so shares x amount = actual dollars.
    Returns list of dicts: date, ticker, shares, div_per_share, div (pre-tax),
    div_after_tax (after div_tax_rate withholding).
    """
    txs = rec[rec["date"] <= pd.Timestamp(date)].sort_values("date")
    cutoff = pd.Timestamp(date)
    rows = []
    for tkr in sorted(txs["ticker"].unique()):
        divs = get_dividends(tkr)
        if len(divs) == 0:
            continue
        txs_tkr = txs[txs["ticker"] == tkr].sort_values("date")
        tx_it = txs_tkr.iterrows()
        tx = next(tx_it, None)
        shares = 0.0
        for _, d in divs.iterrows():
            if d["date"].date() > cutoff.date():
                continue
            while tx is not None and tx[1]["date"] < d["date"]:
                shares += tx[1]["canon_shares"] if tx[1]["side"] == "BUY" else -tx[1]["canon_shares"]
                tx = next(tx_it, None)
            if shares > 1e-9:
                dps = d["amount"]
                div = shares * dps
                rows.append({
                    "date": d["date"],
                    "ticker": tkr,
                    "shares": shares,
                    "div_per_share": dps,
                    "div": div,
                    "div_after_tax": div * (1 - div_tax_rate),
                })
    return sorted(rows, key=lambda r: (r["date"], r["ticker"]))


def print_dividend_breakdown(rows, div_tax_rate):
    print(f"\nDIVIDENDS EARNED (pre-tax / post-tax @ {div_tax_rate:.0%}, up to snapshot date)")
    print("=" * 74)
    print(f"{'Date':<12}{'Ticker':<8}{'Shares':>10}{'Div/share':>10}{'Div $':>10}{'Div after tax':>14}")
    print("-" * 74)
    tot_sh = tot_div = tot_after = 0.0
    for r in rows:
        print(f"{str(r['date'].date()):<12}{r['ticker']:<8}{r['shares']:>10,.4f}"
              f"{r['div_per_share']:>10,.4f}{r['div']:>10,.2f}{r['div_after_tax']:>14,.2f}")
        tot_sh += r["shares"]
        tot_div += r["div"]
        tot_after += r["div_after_tax"]
    print("-" * 74)
    print(f"{'TOTAL':<12}{'':<8}{tot_sh:>10,.4f}{'':>10}{tot_div:>10,.2f}{tot_after:>14,.2f}")


def _ordinal(n):
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{'st' if n % 10 == 1 else 'nd' if n % 10 == 2 else 'rd' if n % 10 == 3 else 'th'}"


def print_series(rows, schedule, dom, method):
    if schedule == "mdom":
        label = f"monthly on the {_ordinal(dom)}, next trading day if holiday"
    else:
        label = "last trading day of each month"
    print(f"\nPortfolio total series ({label}) [{method.upper()}]")
    print("=" * 97)
    print(f"{'Date':<12}{'Market Value':>14}"
          f"{'P&L excl div':>15}{'Div pre-tax':>14}{'Div post-tax':>14}"
          f"{'P&L incl div':>15}")
    print("-" * 97)
    for r in rows:
        print(f"{r['date'].date()!s:<12}{r['value']:>14,.2f}"
              f"{r['total_pnl_excl_div']:>15,.2f}{r['dividend_pre']:>14,.2f}"
              f"{r['dividend_post']:>14,.2f}{r['total_pnl_incl_div']:>15,.2f}")


def main():
    ap = argparse.ArgumentParser(description="Portfolio holdings + PnL snapshot with split reconciliation")
    ap.add_argument("--input", default=None,
                    help="Path to transactions CSV (default: latest transactions_*.csv in the output dir)")
    ap.add_argument("--date", required=True, help="Snapshot date, YYYY-MM-DD (or last month if --monthly/--series)")
    ap.add_argument("--tolerance", type=float, default=0.15,
                    help="Match tolerance raw vs adjusted price (fraction)")
    ap.add_argument("--div-tax-rate", type=float, default=DIV_TAX_RATE,
                    help=f"Withholding tax rate applied to dividends for the "
                         f"'incl div' figures (default {DIV_TAX_RATE:.0%})")
    ap.add_argument("--monthly", action="store_true",
                    help="Show month-end holdings + PnL since first purchase")
    ap.add_argument("--monthly-breakdown", action="store_true",
                    help="Show one PER-TICKER holdings + PnL table for every month-end "
                         "since first purchase (no --series)")
    ap.add_argument("--series", action="store_true",
                    help="Show portfolio TOTALS only, one row per snapshot date (no per-ticker breakdown)")
    ap.add_argument("--schedule", choices=["month-end", "mdom"], default="month-end",
                    help="Date rule for --series: 'month-end' (last trading day of month) "
                         "or 'mdom' (the Nth calendar day, snapped to next trading day)")
    ap.add_argument("--day-of-month", type=int, default=4,
                    help="Day of month for --schedule mdom (default 4)")
    ap.add_argument("--start", type=str, default=None,
                    help="First snapshot date to include (default: first trade)")
    ap.add_argument("--method", choices=["fifo", "lifo"], default="fifo",
                    help="Lot-matching method for realized PnL (default: fifo)")
    ap.add_argument("--dividends", action="store_true",
                    help="Show per-symbol/per-date dividend breakdown (shares, div/share, "
                         "pre-tax div, after-tax div) while holding, up to --date")
    args = ap.parse_args()

    input_path = args.input or latest_transactions()
    df = load_transactions(input_path)
    rec = normalize(df, args.tolerance)

    if args.dividends:
        print_dividend_breakdown(dividend_breakdown(rec, args.date, args.div_tax_rate), args.div_tax_rate)
        return

    if args.series:
        dates = series_dates(rec, args.date, args.schedule, args.day_of_month, args.start)
        rows = portfolio_series(rec, dates, args.method, args.div_tax_rate)
        print_series(rows, args.schedule, args.day_of_month, args.method)
        return

    if args.monthly_breakdown:
        monthly_breakdown(rec, args.date, args.method)
        return

    if args.monthly:
        monthly_report(df, rec, args.tolerance, args.date, args.method)
        return

    snap_date = pd.Timestamp(args.date)
    holdings, realized, over_sell, dividends = build_snapshot(rec, snap_date, args.method)
    flagged = rec[rec["flag_reason"] != ""]

    if len(flagged):
        print(f"\nNOTE: {len(flagged)} transaction(s) could not be fully reconciled;")
        print("      they are listed in the FLAGGED section below.")

    print(f"\nSnapshot date: {snap_date.date()}")
    print("=" * 78)
    print(f"{'Ticker':<8}{'Shares':>12}{'Cost $':>14}{'Market Value':>14}{'Unrealized $':>14}{'P&L %':>9}")
    print("-" * 78)
    tot_cost = tot_val = 0.0
    for h in holdings:
        pct = (h["unrealized"] / h["cost_basis"] * 100) if h["unrealized"] is not None and h["cost_basis"] else 0.0
        val = h["market_value"] if h["market_value"] is not None else 0.0
        pnl = h["unrealized"] if h["unrealized"] is not None else 0.0
        if h["unrealized"] is not None:
            tot_cost += h["cost_basis"]
            tot_val += val
        print(f"{h['ticker']:<8}{h['shares']:>12,.4f}{h['cost_basis']:>14,.2f}"
              f"{val:>14,.2f}{pnl:>14,.2f}{pct:>8.1f}%")
    if holdings:
        tot_pnl = tot_val - tot_cost
        pct = tot_pnl / tot_cost * 100 if tot_cost else 0.0
        print("-" * 78)
        print(f"{'TOTAL':<8}{'':>12}{tot_cost:>14,.2f}{tot_val:>14,.2f}{tot_pnl:>14,.2f}{pct:>8.1f}%")

    if realized:
        div_by_tkr = {t: dividends.get(t, 0.0) for t in realized}
        cap_gains = {t: v - div_by_tkr[t] for t, v in realized.items()}
        print(f"\nREALIZED P&L ({args.method.upper()}, cap gains, up to snapshot date):")
        print("-" * 78)
        for tkr, val in sorted(cap_gains.items()):
            print(f"  {tkr:<8}{val:+,.2f}")
        print(f"  {'TOTAL':<8}{sum(cap_gains.values()):+,.2f}")

    if dividends:
        tax = args.div_tax_rate
        print(f"\nDIVIDENDS RECEIVED (pre-tax / post-tax @ {tax:.0%}, up to snapshot date):")
        print("-" * 78)
        for tkr, val in sorted(dividends.items()):
            print(f"  {tkr:<8}{val:>10,.2f}  /  {val * (1 - tax):>10,.2f}")
        div_total = sum(dividends.values())
        print(f"  {'TOTAL':<8}{div_total:>10,.2f}  /  {div_total * (1 - tax):>10,.2f}")

    if over_sell:
        print("\nWARNING: not enough shares to cover some sells (over-sold):")
        print("-" * 78)
        for tkr, d, sh, rem in over_sell:
            print(f"  {d.date()} {tkr:<6} sold {sh:>10.4f} but only {sh-rem:>.4f} available")

    if not holdings and not realized:
        print("No transactions on or before snapshot date.")

    if len(flagged):
        print("\nFLAGGED (basis not reconciled cleanly — verify):")
        print("-" * 78)
        for _, r in flagged.iterrows():
            print(f"  {r['date'].date()} {r['ticker']:<6}{r['side']:<5}"
                  f"{r['shares']:>10.4f} @ {r['price']:,.2f}  -> {r['flag_reason']}")

    total_pnl = (tot_val - tot_cost) + sum(realized.values()) if holdings else sum(realized.values())
    div_total = sum(dividends.values())
    tax = args.div_tax_rate
    print("=" * 78)
    print(f"NET P&L (pre-dividend, unrealized + realized):     {total_pnl - div_total:+,.2f}")
    print(f"NET P&L (post-dividend, pre-tax dividends):        {total_pnl:+,.2f}")
    print(f"NET P&L (post-dividend, after {tax:.0%} div tax): {total_pnl - tax * div_total:+,.2f}")


if __name__ == "__main__":
    main()