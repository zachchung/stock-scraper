---
name: stock-research
description: >
  Use when the user asks for a single-stock research snapshot (V, AAPL, MSFT,
  DIS, etc.) covering analyst upside, % down from ATH, annual EPS growth
  history, TTM P/E, market cap, and net profit margin — plus a moat
  assessment and a score. IMPORTANT: the report must show the FULL annual EPS
  history (every fiscal year the script returns, typically 10-20 years after
  EDGAR backfill), NEVER truncated to just 5 years, AND must include the
  terminal EPS bar chart (run the script with --chart). Uses the local
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

ALWAYS run with `--chart` so the bar chart is produced:

```bash
.venv/bin/python scripts/stock_research.py <SYMBOL> [<SYMBOL2> ...] [--chart]
```

- `<SYMBOL>` — ticker(s) (e.g. `V`, `AAPL`, `DIS`, `META`), optional, defaults
  to `V`. Pass multiple tickers to get one combined metrics table (one row per
  symbol) plus a separate EPS table + chart for each symbol.
- `--chart` — required; renders each symbol's annual EPS history as a terminal
  bar chart (same chart as `scripts/eps_growth.py --chart`).

The script prints a snapshot with: current price/date, market cap, TTM &
forward P/E, net profit margin, ROE, analyst target + upside %, % down from
ATH, 52wk high/low, the full annual EPS history (every available fiscal year
with YoY % and a last-5-yr CAGR line), the EPS bar chart, and a valuation
section. The script outputs plain **key-value lines** (readable in a
terminal). The transposed Markdown tables in "Report format" below are ONLY
for the agent-written report, never for the script's own stdout.

## Report format

Present the output to the user as **transposed Markdown tables: metrics as
columns, the stock symbol as rows** (mirroring the script's sections). Do
NOT use a `Metric | Value` two-column layout. The example below is
abbreviated for illustration only.

```bash
.venv/bin/python scripts/stock_research.py V
```

| Stock | Analyst upside | % down from ATH | TTM P/E | Market cap | Net profit margin | Return on equity |
|---|---|---|---|---|---|---|
| V | **+15.2%** (mean PT $416.20, 37 analysts, Strong Buy) | **-3.8%** (ATH $375.51; 52wk hi $373.97 / lo $293.89) | **30.9x** (Fwd P/E 24.1x) | **$674.6B** | **50.8%** | 61.2% |

Annual diluted EPS (fiscal year) — **reproduce ALL fiscal years the script
prints as columns**, oldest to newest, with EPS and YoY as rows. This example
is abbreviated; V only has these 4 years locally:

| Metric | FY2022 | FY2023 | FY2024 | FY2025 |
|---|---|---|---|---|
| EPS | $7.00 | $8.28 | $9.73 | $10.20 |
| YoY | — | +18.3% | +17.5% | +4.8% |

For a ticker with full backfilled history, include every year the script
returns as a column — e.g. AAPL from FY2007 ($0.14) through FY2025 ($7.46),
each with its YoY % (the oldest year has no YoY). 5-yr CAGR ≈ **+7.4%** for
AAPL. Note: MA has 19 fiscal years (FY2007–FY2025) after the EDGAR backfill —
never truncate.

With **multiple symbols**, emit a SINGLE metrics table with one row per
symbol, and a SINGLE combined EPS table with one row per company — columns are
the union of all fiscal years (ascending), cells show `$EPS (YoY%)`, and
missing years are left as `—` — followed by one bar chart per symbol in the
order given. Example for 2 symbols: one 2-row metrics table + one 2-row EPS
table + 2 charts. Do not emit a separate EPS table per symbol.

## EPS bar chart (ALWAYS include)

The script prints a horizontal bar chart of split-adjusted annual EPS
(█ positive year, ▒ loss year), with the EPS value and YoY % in brackets on
each bar. **Reproduce this chart in the report** inside a fenced code block,
right after the EPS table, copying every line the script prints (abbreviated
example only):

```text
annual EPS (split-adjusted), █ positive / ▒ loss year:
  2007  █                                            0.14 (--)
  2008  █                                            0.24 (+72.5%)
  ...
  2025  ████████████████████████████████████████     7.46 (+22.7%)
```

Do NOT omit or summarize the chart — the user expects the full rendered
chart. If the script errors, fall back to building the same chart manually
from the EPS table (bar length proportional to |EPS|, max bar = 40 chars).

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
