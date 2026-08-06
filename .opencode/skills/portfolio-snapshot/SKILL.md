---
name: portfolio-snapshot
description: >
  Use when the user pastes a block of transaction data (GOOGL/stock buys+sells)
  in the tab-separated 14-column pair format and asks "how much am I holding
  and what is the PnL", without prior explanation. Also use for HOLDINGS + PnL
  reporting over time from that data: monthly comparison, per-stock monthly
  breakdown, or portfolio-totals-only series (e.g. "what's my PnL at the end of
  every month", "show one table per month", "monthly totals only"). The skill
  knows the exact paste structure/rules, parses it to a CSV, and reports
  holdings + PnL as a FIFO vs LIFO comparison table (single date) or as a
  month-over-month table via the snapshot CLI. DO NOT use for general stock
  questions or financial advice outside this specific analysis.
---

# Portfolio Snapshot

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
   | Cost basis | $15,631.68 | $10,462.66 |
   | Market value | $16,025.85 | $16,025.85 |
   | Total P&L (excl div) | +9,051.09 | +9,051.09 |
   | Dividend (pre-tax) | +173.86 | +173.86 |
   | Dividend (post-tax) | +121.70 | +121.70 |
   | Total P&L (incl div) | +9,172.79 | +9,172.79 |
   | Avg Net Cost $ | 154.99 | 154.99 |

   **ALWAYS show Total P&L in dollar amount.** `Total P&L (excl div)` = unrealized
   + realized capital gains, **excluding** dividend income. `Dividend (pre-tax)`
   = gross dividends received (per ticker from the script's `DIVIDENDS RECEIVED`
   section). `Dividend (post-tax)` = pre-tax × (1 − 0.30) — the 30% dividend
   withholding tax. `Total P&L (incl div)` = `Total P&L (excl div)` + `Dividend
   (post-tax)`. All P&L rows are IDENTICAL for FIFO and LIFO — matching method
   only shifts P&L between realized and unrealized, and dividends are
   method-invariant. Use that invariance as a sanity check. Pull Shares / Cost /
   Market Value / Realized (cap gains) / Dividends from each run's output; the
   script's `NET P&L (pre-dividend)` = Total P&L excl div and `NET P&L
   (post-dividend, after 30% div tax)` = Total P&L incl div. `Avg Net Cost $` =
   (Cost basis − Realized P&L) / Shares held and is also method-invariant.

   For **multi-symbol** pastes, show a per-symbol table with **Total P&L (excl
   div)**, **Dividend (pre-tax)**, **Dividend (post-tax)**, and **Total P&L (incl
   div)** columns, plus an **Avg Net Cost $ column**, and a TOTAL row:

   | Ticker | Shares | Cost $ | Market Value | Total P&L (excl div) | Dividend (pre-tax) | Dividend (post-tax) | Total P&L (incl div) | Avg Net Cost $ |
   |---|---|---|---|---|---|---|---|---|
   | GOOGL | 45 | 15,631.68 | 16,025.85 | +9,051.09 | +173.86 | +121.70 | +9,172.79 | 154.99 |
   | AMZN | 44 | 9,304.82 | 11,949.52 | +5,309.58 | +120.63 | +84.44 | +5,394.02 | 150.91 |
   | TOTAL | | 61,121.96 | 71,470.19 | +32,233.04 | +294.49 | +206.14 | +32,439.18 | |

   **ALWAYS include the Avg Net Cost $ column in the output table.**

   Derivation of Avg Net Cost $ (method-invariant):
   ```
   Avg Net Cost = (Cost basis − Realized P&L) / Shares held
               = (Total invested − Total proceeds) / Shares held
   ```
   - `Cost basis` = remaining cost of shares still held (the `Cost $` column).
   - `Realized P&L` = proceeds minus cost of sold shares (cap gains only — do
     NOT include dividends).
   - Because `Cost basis − Realized P&L = Total invested − Total proceeds`,
     this equals the net cash deployed into the position per share still held,
     and it is IDENTICAL for FIFO and LIFO (matching method only shifts P&L
     between realized and unrealized). Use this invariance as a sanity check.
   - Can be negative (e.g. NVDA −$5.80) when cumulative sale proceeds exceed
     cumulative purchase dollars while shares are still held.

   `Dividend (post-tax)` = `Dividend (pre-tax)` × (1 − 0.30). `Total P&L (incl
   div) − Total P&L (excl div)` = post-tax dividends received on that symbol.
   Pull per-symbol dividends (pre-tax) from the script's `DIVIDENDS RECEIVED`
   section (they are already scaled to current post-split share counts) and
   apply the 30% tax yourself; or read the post-tax total from the script's
   `NET P&L (post-dividend, after 30% div tax)` line. The tax rate is
   configurable via `--div-tax-rate` on the script.

   Then the FIFO-vs-LIFO summary across the whole portfolio (with Total P&L
   excl/incl div as the last columns). If any rows are FLAGGED in the output,
   list them with the reason and explain that their basis was estimated via the
   split-invariant dollar/adjusted-close fallback.

   ## Portfolio-total series (no per-ticker breakdown)

   Use `--series` to get a table with the SAME columns as the per-ticker table
   (`Shares | Cost $ | Market Value | Total P&L (excl div) | Dividend (pre-tax)
   | Dividend (post-tax) | Total P&L (incl div) | Avg Net Cost $`), but one row
   per snapshot date, where each row is the TOTAL across ALL tickers (i.e. the
   TOTAL row of the normal snapshot). This gives a quick month-over-month view
   of the whole book without the per-stock detail.

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
   `Total P&L (excl div)` = Unrealized + Realized capital gains (no dividends);
   `Dividend (pre-tax)` = gross dividends received up to that date;
   `Dividend (post-tax)` = pre-tax × (1 − 0.30); `Total P&L (incl div)` = excl
   div + Dividend (post-tax); `Avg Net Cost $` = (Cost − Realized) / Shares,
   blended across the whole portfolio.

   ## Monthly comparison WITH stock breakdowns

   Use `--monthly-breakdown` to print one full PER-TICKER table per month-end
   (same columns as the default snapshot, plus a per-ticker `Total P&L (excl
   div)` and `Total P&L (incl div)`, and a TOTAL row), from first trade to
   `--date`:

   ```bash
   .venv/bin/python scripts/portfolio_snapshot.py --input txs.csv \
       --date 2026-08-04 --monthly-breakdown            # FIFO
   .venv/bin/python scripts/portfolio_snapshot.py --input txs.csv \
       --date 2026-08-04 --monthly-breakdown --method lifo
   ```

   This is the flag-ified version of the old "one table per month with
   breakdowns" workflow (run the default snapshot per month-end).

   ## Which command when? (prompt → flag)

   | User asks | Run |
   |---|---|
   | "how much am I holding / what's my PnL" on one date | default (no flag) |
   | "one table per month with breakdowns per stock" | `--monthly-breakdown` |
   | "totals only, one row per date, monthly comparison" | `--series --schedule mdom --day-of-month N` or `--schedule month-end` |
   | already-generated single-date totals (also jobs the seed `--monthly`) | `--monthly` |

   Natural-language prompts map to:
   - "…at the end of every month for the last N years, one table per month"
     → `--monthly-breakdown`
   - "…monthly comparison, totals only, on the 4th (or next trading day)"
     → `--series --schedule mdom --day-of-month 4`
   - "…totals only at the end of each month" → `--series --schedule month-end`

## Implementation notes (script behavior)

- `scripts/portfolio_snapshot.py` reconciles raw pre-split vs adjusted records
  against the local `corporate_actions` table (splits + dividends, populated by
  `scraper.py --corporate-actions`; falls back to a live yfinance fetch if a
  ticker isn't ingested), applies split scaling (GOOGL 20:1 on 2022-07-18),
  matches sells FIFO or LIFO per `--method`, and prices holdings at the last
  local close on/before the snapshot date. Splits/dividends are read via
  DuckDB `iceberg_scan` (active snapshot only — raw parquet globs would
  double-count across stale Iceberg snapshots).
- Dividend income is folded into realized PnL; the script prints capital gains
  (`REALIZED P&L`) and `DIVIDENDS RECEIVED (pre-tax / post-tax @ 30%)`
  separately, and three NET lines: `NET P&L (pre-dividend)`, `NET P&L
  (post-dividend, pre-tax dividends)`, `NET P&L (post-dividend, after 30% div
  tax)`. The after-tax line maps directly to the `Total P&L (incl div)` column;
  the tax rate is configurable via `--div-tax-rate` (default 0.30).
- Local prices come from `data/stocks/ohlcv_daily/data/symbol=GOOGL/*.parquet`
  (split-adjusted) queried via DuckDB.
- `--monthly` prints a month-end holdings + PnL table since first purchase
  (same method as `--method`).
- `--monthly-breakdown` prints one PER-TICKER holdings + PnL table per month-end
  since first purchase.
- `--series` prints a portfolio-TOTAL-only table, one row per snapshot date
  (`--schedule mdom --day-of-month N` or `--schedule month-end`).
