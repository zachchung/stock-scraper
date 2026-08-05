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
   | Total P&L ($) | +$9,051.09 | +$9,051.09 |
   | Avg Net Cost $ | 154.99 | 154.99 |

   **ALWAYS show Total P&L in dollar amount** as the last row (Total P&L =
   realized + unrealized). Total P&L must be identical for FIFO and LIFO
   (matching method only shifts P&L between realized and unrealized) — use it
   as a sanity check. Pull shares/cost/value/unrealized/realized from each run's
   output (Total P&L = NET P&L line at the bottom of the script output).
   Avg Net Cost $ = (Cost basis − Realized P&L) / Shares held and is also
   method-invariant (same for FIFO and LIFO).

   For **multi-symbol** pastes, show a per-symbol table with a **Total P&L $
   column** (Total P&L = realized + unrealized, per symbol) and an **Avg Net
   Cost $ column**, plus a TOTAL row:

   | Ticker | Shares | Cost $ | Market Value | Unrealized $ | Realized $ | Total P&L $ | Avg Net Cost $ |
   |---|---|---|---|---|---|---|---|
   | GOOGL | 45 | 15,631.68 | 16,025.85 | +394.17 | +8,656.92 | +9,051.09 | 154.99 |
   | AMZN | 44 | 9,304.82 | 11,949.52 | +2,644.70 | +2,664.88 | +5,309.58 | 150.91 |
   | TOTAL | | 61,121.96 | 71,470.19 | +10,348.23 | +21,884.81 | +32,233.04 | |

   **ALWAYS include the Avg Net Cost $ column in the output table.**

   Derivation of Avg Net Cost $ (method-invariant):
   ```
   Avg Net Cost = (Cost basis − Realized P&L) / Shares held
               = (Total invested − Total proceeds) / Shares held
   ```
   - `Cost basis` = remaining cost of shares still held (the `Cost $` column).
   - `Realized P&L` = proceeds minus cost of sold shares.
   - Because `Cost basis − Realized P&L = Total invested − Total proceeds`,
     this equals the net cash deployed into the position per share still held,
     and it is IDENTICAL for FIFO and LIFO (matching method only shifts P&L
     between realized and unrealized). Use this invariance as a sanity check.
   - Can be negative (e.g. NVDA −$5.80) when cumulative sale proceeds exceed
     cumulative purchase dollars while shares are still held.

   Then the FIFO-vs-LIFO summary across the whole portfolio (with Total P&L $
   as the last row). If any rows are FLAGGED in the output, list them with the
   reason and explain that their basis was estimated via the split-invariant
   dollar/adjusted-close fallback.

   ## Portfolio-total series (no per-ticker breakdown)

   Use `--series` to get a table with the SAME columns as the per-ticker table
   (`Shares | Cost $ | Market Value | Unrealized $ | Realized $ | Total P&L $ |
   Avg Net Cost $`), but one row per snapshot date, where each row is the TOTAL
   across ALL tickers (i.e. the TOTAL row of the normal snapshot). This gives a
   quick month-over-month view of the whole book without the per-stock detail.

   Date rule is chosen with `--schedule`:
   - `--schedule mdom --day-of-month N` (default N=4): the Nth calendar day of
     each month, snapped FORWARD to the next trading day if it lands on a
     holiday/weekend (e.g. Jul 4 → Jul 5, a Saturday → Monday). Column header
     says "next trading day if holiday".
   - `--schedule month-end` (default): the last trading day of each month.

   Trading days are taken from the S&P 500 index parquet
   (`data/stocks/ohlcv_daily/data/symbol=*GSPC/*.parquet`).

   ```bash
   # snapshot on the 4th of each month (or next trading day), FIFO
   .venv/bin/python scripts/portfolio_snapshot.py --input txs.csv \
       --date 2026-08-04 --series --schedule mdom --day-of-month 4
   # last trading day of each month, LIFO
   .venv/bin/python scripts/portfolio_snapshot.py --input txs.csv \
       --date 2026-08-04 --series --schedule month-end --method lifo
   ```

   Per row: `Shares` = total shares held; `Cost $` = total remaining cost basis;
   `Market Value` = total market value (sum of holdings with a price);
   `Unrealized $` = Market Value − Cost; `Realized $` = cumulative realized P&L
   up to that date; `Total P&L $` = Unrealized + Realized; `Avg Net Cost $` =
   (Cost − Realized) / Shares, blended across the whole portfolio.

   ## Monthly comparison WITH stock breakdowns

   Use `--monthly-breakdown` to print one full PER-TICKER table per month-end
   (same columns as the default snapshot, plus a per-ticker `Realized $` and
   `Total P&L $`, and a TOTAL row), from first trade to `--date`:

   ```bash
   .venv/bin/python scripts/portfolio_snapshot.py --input txs.csv \
       --date 2026-08-04 --monthly-breakdown            # FIFO
   .venv/bin/python scripts/portfolio_snapshot.py --input txs.csv \
       --date 2026-08-04 --monthly-breakdown --method lifo
   ```

   This is the flag-ified version of the old "one table per month with
   breakdowns" workflow (run the default snapshot per month-end).

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
- `--monthly-breakdown` prints one PER-TICKER holdings + PnL table per month-end
  since first purchase.
- `--series` prints a portfolio-TOTAL-only table, one row per snapshot date
  (`--schedule mdom --day-of-month N` or `--schedule month-end`).
