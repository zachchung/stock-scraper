#!/usr/bin/env python3
"""
Portfolio snapshot: holdings + PnL on any date, with automatic split reconciliation.

Data sources
  - Splits .......... yfinance .splits (authoritative ratios + effective dates)
  - Prices .......... local stock_scraper OHLCV (already split-adjusted), yfinance fallback
  - Transactions .... your CSV file (see format below)

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
import glob
import json
import os
import sys

import duckdb
import pandas as pd
import yfinance as yf

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CON = duckdb.connect()
SPLIT_CACHE = os.path.join(BASE_DIR, ".portfolio_splits.json")


def load_transactions(path):
    df = pd.read_csv(path)
    need = {"date", "ticker", "side", "shares", "price"}
    missing = need - set(df.columns.str.lower())
    if missing:
        sys.exit(f"Missing columns: {sorted(missing)} (expected {sorted(need)})")
    df.columns = [c.lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"])
    df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()
    df["side"] = df["side"].astype(str).str.upper().str.strip()
    df["shares"] = df["shares"].astype(float)
    df["price"] = df["price"].astype(float)
    bad = ~df["side"].isin({"BUY", "SELL"})
    if bad.any():
        sys.exit(f"Invalid side values: {df.loc[bad, 'side'].unique().tolist()}")
    return df.sort_values("date").reset_index(drop=True)


def get_splits(ticker):
    """DataFrame [date, factor]; factor = new shares per 1 old share. Cached on disk."""
    cache = {}
    if os.path.exists(SPLIT_CACHE):
        with open(SPLIT_CACHE) as fh:
            cache = json.load(fh)
    if ticker in cache:
        df = pd.DataFrame(cache[ticker])
        if len(df):
            df["date"] = pd.to_datetime(df["date"])
        return df

    raw = yf.Ticker(ticker).splits  # Series: DatetimeIndex -> ratio
    recs = []
    if raw is not None and len(raw):
        for idx, ratio in raw.items():
            recs.append({"date": str(idx.date()), "factor": float(ratio)})
    cache[ticker] = recs
    with open(SPLIT_CACHE, "w") as fh:
        json.dump(cache, fh, indent=2)
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
        return None if row is None else row[0]
    except Exception:
        return None


def normalize(df, tolerance):
    """Normalize every transaction to CURRENT post-split share count.

    Cost basis (shares * price) is invariant under splits, so we never trust
    the recorded share count alone. Instead we reconcile against the local
    split-adjusted close on the transaction date:

      * If recorded price matches the adjusted level -> shares are already current.
      * If recorded price matches the raw (pre-split) level -> shares *= F.
      * If the recorded price level can't be classified but the LOCAL adjusted
        price exists, we ESTIMATE current shares as dollar / adjusted_close,
        which is split-invariant regardless of the recording basis.
      * If no local price, fall back to shares * F.

    Rows that can't be classified are flagged for manual review.
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
        adj = local_price_at(ticker, d)
        cost = row["shares"] * row["price"]

        if adj is None:
            out.at[i, "canon_shares"] = row["shares"] * F
            out.at[i, "flag_reason"] = "no local price; scaled by split factor" if F > 1.0001 else ""
            out.at[i, "basis"] = "fallback*F"
            out.at[i, "cost_basis"] = cost
            continue

        err_adj = abs(row["price"] - adj) / adj
        err_raw = abs(row["price"] - adj * F) / (adj * F) if F > 1 else None

        if err_raw is not None and err_raw < tolerance:
            out.at[i, "canon_shares"] = row["shares"] * F
            out.at[i, "basis"] = "raw"
        elif err_adj < tolerance:
            out.at[i, "canon_shares"] = row["shares"]
            out.at[i, "basis"] = "adjusted"
        else:
            # Cannot match either basis cleanly. Estimate split-invariantly:
            # dollar / adjusted_close = current post-split shares.
            out.at[i, "canon_shares"] = cost / adj
            out.at[i, "basis"] = "est(dollar/adjclose)"
            out.at[i, "flag_reason"] = (
                f"price {row['price']:.2f} not near adj {adj:.2f} or raw {adj*F:.2f}; "
                f"used dollar/adjclose"
            )
        out.at[i, "cost_basis"] = cost  # invariant
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


def build_snapshot(reconciled, date):
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
            while rem > 1e-9 and open_pos.get(tkr):
                head = open_pos[tkr][0]
                take = min(rem, head["shares"])
                sold_cost += take * head["cost"]
                head["shares"] -= take
                rem -= take
                if head["shares"] <= 1e-9:
                    open_pos[tkr].pop(0)
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
    return holdings, realized, over_sell


def main():
    ap = argparse.ArgumentParser(description="Portfolio holdings + PnL snapshot with split reconciliation")
    ap.add_argument("--input", required=True, help="Path to transactions CSV")
    ap.add_argument("--date", required=True, help="Snapshot date, YYYY-MM-DD")
    ap.add_argument("--tolerance", type=float, default=0.15,
                    help="Match tolerance raw vs adjusted price (fraction)")
    args = ap.parse_args()

    df = load_transactions(args.input)
    rec = normalize(df, args.tolerance)
    snap_date = pd.Timestamp(args.date)
    holdings, realized, over_sell = build_snapshot(rec, snap_date)
    flagged = rec[rec["flag_reason"] != ""]

    print(f"\nSnapshot date: {snap_date.date()}")
    print("=" * 78)
    print(f"{'Ticker':<8}{'Shares':>12}{'Cost $':>14}{'Value $':>14}{'Unrealized $':>14}{'P&L %':>9}")
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
        print("\nREALIZED P&L (FIFO, up to snapshot date):")
        print("-" * 78)
        for tkr, val in sorted(realized.items()):
            print(f"  {tkr:<8}{val:+,.2f}")
        print(f"  {'TOTAL':<8}{sum(realized.values()):+,.2f}")

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
    print("=" * 78)
    print(f"NET P&L (realized + unrealized): {total_pnl:+,.2f}")


if __name__ == "__main__":
    main()