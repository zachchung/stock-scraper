---
name: stock-research
description: >
  Use when the user asks for a single-stock research snapshot (V, AAPL, MSFT,
  DIS, etc.) covering analyst upside, % down from ATH, annual EPS growth
  history, TTM P/E, market cap, and net profit margin — plus a moat
  assessment and a score. IMPORTANT: the report must show the FULL annual EPS
  history (every fiscal year the script returns, typically 10-20 years after
  EDGAR backfill), NEVER truncated to just 5 years. Uses the local
  stock_scraper DuckDB dataset (scripts/stock_research.py), falling back to
  yfinance only for EPS history when fewer than 5 annual records exist
  locally. DO NOT use for general stock questions or financial advice outside
  this specific research-snapshot report.
---

# Stock Research Snapshot

Produces a reusable single-stock research report combining the local
stock_scraper dataset (queried via DuckDB) with a qualitative moat/score
write-up. The `scripts/stock_research.py` script computes all quantitative
metrics; the agent adds the qualitative moat analysis and a score out of 10.

## Where data lives

Queried through the local `stocks.duckdb` database (falls back to reading the
Iceberg/Parquet files directly if the DB is locked):

```
stocks.duckdb
├── fundamentals_snapshot   → market cap, TTM/forward P/E, net profit margin, ROE, ATH, 52wk range
├── analyst_targets         → mean/high/low price targets, recommendation, analyst count
├── ohlcv                   → latest close (current price)
├── income_statements       → annual diluted EPS history (frequency='annual')
└── corporate_actions       → split history (used to normalize EPS across splits)
```

## Running

```bash
.venv/bin/python scripts/stock_research.py <SYMBOL>
.venv/bin/python scripts/stock_research.py <SYMBOL> --chart
```

- `<SYMBOL>` — ticker (e.g. `V`, `AAPL`, `DIS`, `META`), optional, defaults to `V`.
- `--chart` — also render the annual EPS history as a terminal bar chart
  (same chart as `scripts/eps_growth.py --chart`).

The script prints a snapshot with: current price/date, market cap, TTM &
forward P/E, net profit margin, ROE, analyst target + upside %, % down from
ATH, 52wk high/low, the full annual EPS history (every available fiscal year
with YoY % and a last-5-yr CAGR line), and a valuation section.

## Report format

Present the output to the user as a Markdown table, mirroring the script's
sections. **The annual EPS table must include EVERY fiscal year the script
prints** — AAPL/MSFT/MU/GOOGL have 13–19 years after the EDGAR backfill. Do
NOT truncate it to the last 5 years. The example below is abbreviated for
illustration only.

```bash
.venv/bin/python scripts/stock_research.py V
```

| Metric | Value |
|---|---|
| Analyst upside | **+15.2%** (mean PT $416.20, 37 analysts, Strong Buy) |
| % down from ATH | **-3.8%** (ATH $375.51; 52wk hi $373.97 / lo $293.89) |
| TTM P/E | **30.9x** (Fwd P/E 24.1x) |
| Market cap | **$674.6B** |
| Net profit margin | **50.8%** |
| Return on equity | 61.2% |

Annual diluted EPS (fiscal year) — **reproduce ALL rows the script prints**,
oldest to newest. This example is abbreviated; V only has these 4 years
locally:

| FY | EPS | YoY |
|---|---|---|
| FY2022 | $7.00 | — |
| FY2023 | $8.28 | +18.3% |
| FY2024 | $9.73 | +17.5% |
| FY2025 | $10.20 | +4.8% |

For a ticker with full backfilled history, list every year the script
returns — e.g. AAPL from FY2007 ($0.14) through FY2025 ($7.46), each with its
YoY % (the oldest year has no YoY). 5-yr CAGR ≈ **+7.4%** for AAPL.

Notes:
- FY year labels follow the company's fiscal year end (the script prints the
  fiscal-month/day end date on each row).
- EPS values are **split-adjusted to the current basis** (`scripts/split_adjust.py`
  detects each year's split basis via implied shares = net_income / diluted_eps),
  so YoY % and CAGR are not distorted by stock splits.
- YoY % = current FY EPS vs the immediately preceding FY.
- The EPS table shows the FULL history; the CAGR line covers the most recent
  5 fiscal years (or fewer if that's all the data has — state the actual
  window in that case).

## Moat and score (agent judgment)

After printing the quantitative table, add a concise qualitative analysis:

1. **Moat analysis** — 2-4 sentences covering the durable competitive
   advantages specific to the company (network effects, switching costs,
   brand, scale, regulation/licensing, pricing power) and the main
   competitive/regulatory threats.
2. **Score** — a single score out of **10** with a one-line justification.
   Calibrate to the strength and durability of the moat (e.g. 9.5 for Visa's
   two-sided payments network; ~8 for Apple; lower for moats under pressure).
