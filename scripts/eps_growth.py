#!/usr/bin/env python3
"""Annual EPS growth, split-adjusted to a common (current) basis.

Reads annual diluted EPS from the local income_statements table and normalizes
for stock splits (which EDGAR/yfinance restate inconsistently across years)
using scripts/split_adjust.py. Prints a per-year YoY table plus a multi-year
CAGR line.

Usage:
  python scripts/eps_growth.py AAPL
  python scripts/eps_growth.py AAPL MSFT --years 10
  python scripts/eps_growth.py AAPL --chart   # also draw a terminal bar chart
"""

import argparse
import os
import sys

import duckdb
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE, "data", "stocks")

import split_adjust


def load_rows(con, symbol):
    con.execute("LOAD iceberg")
    income_path = os.path.join(DATA_DIR, "income_statements")
    splits_path = os.path.join(DATA_DIR, "corporate_actions")
    if not os.path.isdir(income_path) or not os.path.isdir(splits_path):
        print("data/stocks not found; run scraper.py first", file=sys.stderr)
        sys.exit(1)
    df = con.execute(f"""
        SELECT fiscal_date, diluted_eps, net_income
        FROM iceberg_scan('{income_path}')
        WHERE symbol='{symbol}' AND frequency='annual'
        ORDER BY fiscal_date
    """).fetchdf()
    split_rows = con.execute(f"""
        SELECT date, split_factor
        FROM iceberg_scan('{splits_path}')
        WHERE symbol='{symbol}' AND action_type='split' AND split_factor > 1
        ORDER BY date
    """).fetchdf()
    return df, split_rows


def growth_table(df, split_factors, cagr_years):
    df = df.dropna(subset=["diluted_eps"]).copy()
    if df.empty:
        return None
    dates = [d for d in df["fiscal_date"]]
    eps = list(df["diluted_eps"])
    ni = list(df["net_income"])
    df["eps_adj"] = split_adjust.adjusted_eps(dates, eps, ni, split_factors)
    df["yoy_pct"] = (df["eps_adj"] / df["eps_adj"].shift(1) - 1) * 100
    df = df.dropna(subset=["eps_adj"])

    out = df[["fiscal_date", "eps_adj", "yoy_pct"]].copy()
    out["cagr_yrs"] = ""
    tail = df.tail(cagr_years)
    if len(tail) >= 2:
        start = tail.iloc[0]["eps_adj"]
        end = tail.iloc[-1]["eps_adj"]
        cagr = ((end / start) ** (1 / (len(tail) - 1)) - 1) * 100
        out.loc[df.index[-1], "cagr_yrs"] = (
            f"CAGR (last {len(tail)} yrs) "
            f"{tail.iloc[0]['fiscal_date'].year}-{tail.iloc[-1]['fiscal_date'].year}: "
            f"{cagr:+.1f}%"
        )
    return out


def print_bar_chart(table):
    """Horizontal bar chart of split-adjusted annual EPS in the terminal."""
    df = table.dropna(subset=["eps_adj"])
    if df.empty:
        return
    max_abs = max(abs(v) for v in df["eps_adj"]) or 1.0
    width = 40
    tty = sys.stdout.isatty()
    GREEN, RED, RESET = "\033[32m", "\033[31m", "\033[0m"
    print("  annual EPS (split-adjusted), █ positive / ▒ loss year:")
    for _, r in df.iterrows():
        v = r["eps_adj"]
        d = r["fiscal_date"]
        ds = d.strftime("%Y") if hasattr(d, "strftime") else str(d)[:4]
        yoy = r["yoy_pct"]
        pct = f"({yoy:+.1f}%)" if pd.notna(yoy) else "(--)"
        bar = "█" if v >= 0 else "▒"
        n_blocks = int(abs(v) / max_abs * width)
        if v != 0:
            n_blocks = max(1, n_blocks)
        fill = bar * n_blocks
        if tty:
            color = GREEN if v >= 0 else RED
            print(f"  {ds}  {color}{fill}{RESET:<{width}} {v:8.2f} {pct}")
        else:
            print(f"  {ds}  {fill:<{width}} {v:8.2f} {pct}")


def main():
    parser = argparse.ArgumentParser(
        description="Annual EPS growth with stock-split normalization."
    )
    parser.add_argument("tickers", nargs="+", help="Tickers (e.g. AAPL MSFT)")
    parser.add_argument("--years", type=int, default=5,
                        help="CAGR window in years (default: 5)")
    parser.add_argument("--chart", action="store_true",
                        help="Also draw a terminal bar chart of annual EPS")
    args = parser.parse_args()

    con = duckdb.connect()
    for sym in args.tickers:
        sym = sym.upper()
        df, split_rows = load_rows(con, sym)
        split_factors = split_rows["split_factor"].tolist()
        table = growth_table(df, split_factors, args.years)
        print(f"===== {sym} annual EPS growth (split-adjusted) =====")
        if table is None or table.empty:
            print("  no annual EPS data")
            print()
            continue
        print("  FY-end      EPS   YoY%")
        for _, r in table.iterrows():
            d = r["fiscal_date"]
            ds = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
            yoy = f"{r['yoy_pct']:+.1f}%" if pd.notna(r["yoy_pct"]) else "   --"
            print(f"  {ds}  {r['eps_adj']:7.2f}  {yoy}")
        cagr = table["cagr_yrs"].dropna()
        if not cagr.empty:
            print(f"  {cagr.iloc[-1]}")
        if args.chart:
            print_bar_chart(table)
        print()


if __name__ == "__main__":
    main()