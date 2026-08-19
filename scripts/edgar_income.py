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
import calendar
import datetime
import os
import re
import sys

import pandas as pd
import requests

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))

from stock_scraper import scraper

USER_AGENT = "StockScraperResearch/1.0 (local research tool; contact: stock_research@localhost)"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

ANNUAL_FORMS = ("10-K", "10-K/A", "20-F", "20-F/A")

TAG_GROUPS = {
    "total_revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
    ],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss"],
    "diluted_eps": ["EarningsPerShareDiluted"],
    "dep_amort": ["DepreciationDepletionAndAmortization"],
}


def fetch_cik_map():
    resp = requests.get(TICKERS_URL, headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    return {e["ticker"].upper(): int(e["cik_str"]) for e in resp.json().values()}


def resolve_cik(symbol, cik_map):
    for cand in (symbol, symbol.replace(".", "-")):
        cik = cik_map.get(cand)
        if cik:
            return cik
    return None


def fetch_companyfacts(cik):
    resp = requests.get(
        FACTS_URL.format(cik=cik), headers={"User-Agent": USER_AGENT}, timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def annual_values(facts, tags, unit="USD"):
    """Return {fiscal_end: latest_value} for the annual (10-K/20-F) periods.

    Falls back across `tags` (e.g. pre/post ASC 606 revenue tags) and keeps the
    value from the most recent filing (max accession number) per period end.
    """
    out = {}
    usgaap = facts.get("facts", {}).get("us-gaap", {})
    for tag in tags:
        tagdata = usgaap.get(tag)
        if not tagdata:
            continue
        for entry in tagdata.get("units", {}).get(unit, []):
            if entry.get("fp") != "FY":
                continue
            if entry.get("form") not in ANNUAL_FORMS:
                continue
            end = entry.get("end")
            if not end:
                continue
            if not _is_annual_period(end, entry):
                continue
            accn = entry.get("accn", "")
            if end in out and accn <= out[end][0]:
                continue
            out[end] = (accn, entry.get("val"))
    return {k: v[1] for k, v in out.items()}


def _is_annual_period(end, entry):
    """Old 10-K filings embed quarterly frames tagged fp='FY'. An annual period
    spans ~360 days (start->end) or carries an annual frame (CY<year>, no Q)."""
    start = entry.get("start")
    if start:
        try:
            dur = (
                datetime.date.fromisoformat(end)
                - datetime.date.fromisoformat(start)
            ).days
        except ValueError:
            dur = None
        if dur is not None:
            return dur >= 300
    frame = entry.get("frame")
    return not (frame and re.search(r"Q[1-4]$", frame))


def build_rows(symbol, facts):
    series = {}
    for col, tags in TAG_GROUPS.items():
        unit = "USD/shares" if col == "diluted_eps" else "USD"
        series[col] = annual_values(facts, tags, unit)

    ends = set()
    for vals in series.values():
        ends |= set(vals)
    if not ends:
        return pd.DataFrame()

    rows = []
    for end in sorted(ends):
        try:
            fiscal_date = datetime.date.fromisoformat(end)
        except ValueError:
            continue
        # yfinance reports fiscal period ends as month-end (e.g. 2022-09-30);
        # EDGAR reports the exact end (e.g. 2022-09-24). Normalize to month-end
        # so rows merge/align with the existing income_statements data.
        fiscal_date = fiscal_date.replace(
            day=calendar.monthrange(fiscal_date.year, fiscal_date.month)[1]
        )
        rev = series["total_revenue"].get(end)
        opinc = series["operating_income"].get(end)
        d_a = series["dep_amort"].get(end)
        rows.append({
            "symbol": symbol,
            "frequency": "annual",
            "fiscal_date": fiscal_date,
            "total_revenue": rev,
            "gross_profit": series["gross_profit"].get(end),
            "operating_income": opinc,
            "net_income": series["net_income"].get(end),
            "diluted_eps": series["diluted_eps"].get(end),
            "ebit": opinc,
            "ebitda": (opinc + d_a) if (opinc is not None and d_a is not None) else None,
        })

    df = pd.DataFrame(rows)
    metric_cols = [
        "total_revenue", "gross_profit", "operating_income",
        "net_income", "diluted_eps", "ebit", "ebitda",
    ]
    for col in metric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["net_profit_margin"] = df["net_income"] / df["total_revenue"]
    return df


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

    cik_map = fetch_cik_map()
    for raw in args.tickers:
        symbol = raw.upper()
        cik = resolve_cik(symbol, cik_map)
        if cik is None:
            print(f"[{symbol}] no CIK found in SEC tickers, skipping")
            continue
        try:
            facts = fetch_companyfacts(cik)
        except requests.RequestException as e:
            print(f"[{symbol}] EDGAR request failed: {e}")
            continue

        df = build_rows(symbol, facts)
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
