#!/usr/bin/env python3
"""SEC EDGAR companyfacts helpers: build annual income-statement rows.

Used by scripts/edgar_income.py (standalone backfill) and scraper.py's
--earnings flow (automatic backfill beyond yfinance's ~5y window).

Note: EDGAR restates historical EPS/values on mixed split bases (some years
pre-split, some post-split depending on the filing). Split normalization is
handled separately by scripts/split_adjust.py.
"""

import calendar
import datetime
import re
import time

import pandas as pd
import requests

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

_CIK_MAP = None


def cik_map():
    """Ticker -> CIK map, fetched from SEC once and cached."""
    global _CIK_MAP
    if _CIK_MAP is None:
        resp = requests.get(
            TICKERS_URL, headers={"User-Agent": USER_AGENT}, timeout=20
        )
        resp.raise_for_status()
        _CIK_MAP = {e["ticker"].upper(): int(e["cik_str"]) for e in resp.json().values()}
    return _CIK_MAP


def resolve_cik(symbol):
    for cand in (symbol, symbol.replace(".", "-")):
        cik = cik_map().get(cand)
        if cik:
            return cik
    return None


def fetch_companyfacts(cik):
    """Fetch the full XBRL companyfacts JSON for a CIK."""
    resp = requests.get(
        FACTS_URL.format(cik=cik), headers={"User-Agent": USER_AGENT}, timeout=30
    )
    resp.raise_for_status()
    time.sleep(0.12)  # stay under SEC's ~10 req/s limit
    return resp.json()


def _nearest_month_end(d):
    """Round a fiscal period end to the nearest month-end, matching yfinance's
    convention (e.g. 2022-09-24 -> 2022-09-30, but 2022-09-01 -> 2022-08-31)."""
    this_end = datetime.date(
        d.year, d.month, calendar.monthrange(d.year, d.month)[1]
    )
    prev_end = datetime.date(d.year, d.month, 1) - datetime.timedelta(days=1)
    return this_end if (this_end - d) <= (d - prev_end) else prev_end


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


def build_rows(symbol, facts):
    """Build a DataFrame in the income_statements schema (frequency='annual')
    from a companyfacts JSON."""
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
        # yfinance reports fiscal period ends as a month-end (e.g. AAPL
        # 2022-09-24 -> 2022-09-30; MU 2022-09-01 -> 2022-08-31, i.e. the
        # NEAREST month-end). Normalize EDGAR's exact end date the same way so
        # rows merge/align with the existing income_statements data.
        fiscal_date = _nearest_month_end(fiscal_date)
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