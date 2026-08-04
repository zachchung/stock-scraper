---
name: pasted-transactions-analysis
description: >
  Use when the user pastes a block of transaction data (GOOGL/stock buys+sells)
  in the tab-separated 14-column pair format and asks "how much am I holding
  and what is the PnL", without prior explanation. The skill knows the exact
  paste structure/rules, parses it to a CSV, and reports holdings + PnL as a
  FIFO vs LIFO comparison table. DO NOT use for general stock questions or
  financial advice outside this specific analysis.
---

# Pasted Transactions Analysis

Analyzes a pasted trade-history block for ONE OR MULTIPLE tickers (GOOGL,
AMZN, MSFT, AAPL, etc.) and produces holdings + PnL as of today, shown as a
FIFO vs LIFO comparison table per symbol and for the whole portfolio.

## Paste structure (the rules)

Each line is ONE transaction pair, tab-separated, 14 columns:

```
GOOGL\tBUY\t<price>\t<shares>\t<amount>\t<date>\t<ignore>\tGOOGL\tSELL\t<price>\t<shares>\t<amount>\t<date>\t<ignore>
```

Rules:
- **Left side = BUY, right side = SELL.** Buy-only rows have the SELL side
  empty (no price/shares on the right) => those shares are still held.
- **Multi-symbol**: each line carries its own ticker in columns 0 and 7
  (both sides share the same ticker). Blocks of lines for different tickers
  are separated by blank spacer rows (`\tBUY\t...\tSELL\t...`) and optional
  section labels (e.g. `Large cap & pe <100`) — these are skipped.
- Dates are `YY-M/D` (e.g. `26-7/31` => `2026-07-31`, `19-6/3` => `2019-06-03`).
- Amounts/shares may contain commas (e.g. `1,037.00`).
- **No duplicated data, do NOT dedup.** Every row is a distinct trade. Two BUY
  rows at the same price on the same date = two separate trades.
- The transaction CSV consumed by the script MUST NOT be deduplicated.

## Workflow

1. **Save the pasted block** to a temp file and parse it with the parser
   (14-col pair format -> flat CSV with header `date,ticker,side,shares,price`).
   Parser: `/Users/ZacharyChung1/code/stock_scraper/scripts/parse_pasted_txs.py`
   ```bash
   .venv/bin/python scripts/parse_pasted_txs.py < pasted.txt > txs.csv
   ```
   It emits one BUY and/or one SELL row per input line (buy-only lines emit
   only the BUY row). Ticker is assumed to match the left column.
2. **Run the snapshot for BOTH methods** (FIFO and LIFO):
   ```bash
   .venv/bin/python scripts/portfolio_snapshot.py --input txs.csv --date 2026-08-04 --method fifo
   .venv/bin/python scripts/portfolio_snapshot.py --input txs.csv --date 2026-08-04 --method lifo
   ```
   `--date` = today (the last trade date in the data / current date).
3. **Present the comparison table**, always in this format (single symbol):

   | | FIFO | LIFO |
   |---|---|---|
   | Shares held | 45 | 45 |
   | Cost basis | $15,631.68 | $10,434.46 |
   | Market value | $16,025.85 | $16,025.85 |
   | Unrealized P&L | +$394.17 (+2.5%) | +$5,591.39 (+53.6%) |
   | Realized P&L | +$8,656.92 | +$3,459.70 |
   | Net P&L | +$9,051.09 | +$9,051.09 |

   Pull shares/cost/value/unrealized/realized from each run's output. Net P&L
   must be identical for FIFO and LIFO (matching method only shifts P&L between
   realized and unrealized) — use it as a sanity check.

   For **multi-symbol** pastes, show a per-symbol table (Ticker / Shares / Cost /
   Value / Unrealized / P&L %) plus TOTAL from each run, and then the
   FIFO-vs-LIFO summary across the whole portfolio. If any rows are FLAGGED in
   the output, list them with the reason and explain that their basis was
   estimated via the split-invariant dollar/adjusted-close fallback.

## Implementation notes (script behavior)

- `scripts/portfolio_snapshot.py` reconciles raw pre-split vs adjusted records
  against an authoritative yfinance split table (cached in
  `.portfolio_splits.json`, gitignored), applies split scaling (GOOGL 20:1 on
  2022-07-18), matches sells FIFO or LIFO per `--method`, and prices holdings
  at the last local close on/before the snapshot date.
- Local prices come from `data/stocks/ohlcv_daily/data/symbol=GOOGL/*.parquet`
  (split-adjusted) queried via DuckDB.
- `--monthly` prints a month-end holdings + PnL table since first purchase
  (same method as `--method`).
