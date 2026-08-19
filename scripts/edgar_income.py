#!/usr/bin/env python3
"""Backfill annual income statements from SEC EDGAR companyfacts.

yfinance only exposes ~5 years of annual income statement history. SEC EDGAR's
XBRL companyfacts API has the full as-reported history (back to the 1990s for
most US filers). This script builds rows in the exact income_statements schema
(symbol, frequency='annual', fiscal_date, total_revenue, gross_profit,
operating_income, net_income, diluted_eps, ebit, ebitda, net_profit_margin)
and merges them into the same Iceberg table via scraper.write_income_to_iceberg.

By default it only backfills fiscal periods not already present (so recent
yfinance rows are left untouched). Use --update to also refresh existing rows
from EDGAR (authoritative as-reported values, includes restatements).

Usage:
  python scripts/edgar_income.py AAPL MSFT --max-year 2020
  python scripts/edgar_income.py AAPL --update
"""

import argparse
import os
import sys

import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))

from stock_scraper import edgar, scraper


def existing_annual_dates(symbol):
    spark = scraper.get_spark()
    try:
        rows = spark.sql(
            f"SELECT DISTINCT fiscal_date FROM {scraper.INCOME_TABLE} "
            f"WHERE symbol='{symbol}' AND frequency='annual'"
        ).collect()
        return {r[0] for r in rows}
    except Exception:
        return set()


def main():
    parser = argparse.ArgumentParser(
        description="Backfill annual income statements from SEC EDGAR companyfacts."
    )
    parser.add_argument("tickers", nargs="+", help="Tickers to backfill (e.g. AAPL MSFT)")
    parser.add_argument(
        "--max-year", type=int, default=None,
        help="Only load fiscal years <= this year (extend older history)",
    )
    parser.add_argument(
        "--update", action="store_true",
        help="Also refresh already-present rows from EDGAR (default: only backfill missing)",
    )
    args = parser.parse_args()

    for raw in args.tickers:
        symbol = raw.upper()
        cik = edgar.resolve_cik(symbol)
        if cik is None:
            print(f"[{symbol}] no CIK found in SEC tickers, skipping")
            continue
        try:
            facts = edgar.fetch_companyfacts(cik)
        except Exception as e:
            print(f"[{symbol}] EDGAR request failed: {e}")
            continue

        df = edgar.build_rows(symbol, facts)
        if df.empty:
            print(f"[{symbol}] no annual income statement data in EDGAR")
            continue
        if args.max_year is not None:
            df = df[df["fiscal_date"].year <= args.max_year]
        if df.empty:
            print(f"[{symbol}] no annual periods within max-year")
            continue

        if not args.update:
            existing = existing_annual_dates(symbol)
            if existing:
                df = df[~df["fiscal_date"].isin(existing)]
        if df.empty:
            print(f"[{symbol}] no new annual periods to load")
            continue

        loaded = len(df)
        year_range = f"{df['fiscal_date'].min().year}-{df['fiscal_date'].max().year}"
        scraper.write_income_to_iceberg(df)
        print(f"[{symbol}] loaded {loaded} annual periods ({year_range})")


if __name__ == "__main__":
    main()