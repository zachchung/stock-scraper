---
name: stock-research
description: >
  Use when the user asks for a single-stock research snapshot (V, AAPL, MSFT,
  DIS, etc.) covering analyst upside, % down from ATH, annual EPS growth over
  the last 5 years, TTM P/E, market cap, and net profit margin — plus a moat
  assessment and a score. Uses the local stock_scraper DuckDB dataset
  (scripts/stock_research.py), falling back to yfinance only for EPS history
  when fewer than 5 annual records exist locally.
  DO NOT use for general stock questions or financial advice outside this
  specific research-snapshot report.
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
└── income_statements       → annual diluted EPS history (frequency='annual')
```

## Running

```bash
.venv/bin/python scripts/stock_research.py <SYMBOL>
```

- `<SYMBOL>` — ticker (e.g. `V`, `AAPL`, `DIS`, `META`), optional, defaults to `V`.

The script prints a snapshot with: current price/date, market cap, TTM &
forward P/E, net profit margin, ROE, analyst target + upside %, % down from
ATH, 52wk high/low, annual EPS table (last ~5 fiscal years with YoY % and a
CAGR line), and a valuation section.

## Report format

Present the output to the user as a Markdown table, mirroring the script's
sections. Example invocation and expected layout:

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

Annual diluted EPS (fiscal year):

| FY | EPS | YoY |
|---|---|---|
| FY2022 | $7.00 | +24.3% |
| FY2023 | $8.28 | +18.3% |
| FY2024 | $9.73 | +17.5% |
| FY2025 | $10.20 | +4.8% |

5-yr EPS CAGR ≈ **16%**.

Notes:
- FY year labels follow the company's fiscal year end (the script prints the
  fiscal-month/day end date on each row).
- YoY % = current FY EPS vs the immediately preceding FY.
- CAGR is computed over however many annual rows are available (up to 5); if
  the local DB + yfinance together provide fewer than 5 years, state the
  actual number of years shown.

## Moat and score (agent judgment)

After printing the quantitative table, add a concise qualitative analysis:

1. **Moat analysis** — 2-4 sentences covering the durable competitive
   advantages specific to the company (network effects, switching costs,
   brand, scale, regulation/licensing, pricing power) and the main
   competitive/regulatory threats.
2. **Score** — a single score out of **10** with a one-line justification.
   Calibrate to the strength and durability of the moat (e.g. 9.5 for Visa's
   two-sided payments network; ~8 for Apple; lower for moats under pressure).
