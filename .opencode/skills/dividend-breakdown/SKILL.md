---
name: dividend-breakdown
description: >
  Use when the user asks to see all dividends earned while holding stocks from
  their pasted transaction data (e.g. "show me all the dividends I earned",
  "how much dividend did I get on GOOGL", "dividend breakdown per stock per
  date", "pre-tax and post-tax dividends"). Reports one row per symbol/ex-date
  with shares held, dividend per share, pre-tax and after-tax (30%) dividend.
  Understands the same 14-column pasted BUY/SELL pair format as the
  portfolio-snapshot skill. DO NOT use for general stock questions or financial
  advice outside this specific dividend report.
---

# Dividend Breakdown

Shows every dividend received while each position was held, one row per
**symbol x ex-date**, with pre-tax and post-tax (30% withholding) amounts.

Modeled on the `portfolio-snapshot` skill: same pasted transaction format, same
split reconciliation, same local `corporate_actions` dividend source.

## Paste structure (the rules)

Identical to `portfolio-snapshot`: each line is ONE transaction pair,
tab-separated, 14 columns, left = BUY, right = SELL. Buy-only rows have an
empty SELL side (shares still held). Multi-symbol blocks are separated by blank
spacer rows and optional section labels (skipped).

```
GOOGL\tBUY\t<price>\t<shares>\t<amount>\t<date>\t<ignore>\tGOOGL\tSELL\t<price>\t<shares>\t<amount>\t<date>\t<ignore>
```

- Dates are `YY-M/D` (e.g. `26-7/31` => `2026-07-31`).
- Amounts/shares may contain commas.
- **Do NOT dedup** — every row is a distinct trade.

## Workflow

1. **Save the pasted block** to a temp file and parse it to a flat CSV
   (header `date,ticker,side,shares,price`):
   ```bash
   .venv/bin/python scripts/parse_pasted_txs.py < pasted.txt > txs.csv
   ```
2. **Run the dividend breakdown** up to today (the last trade/current date):
   ```bash
   .venv/bin/python scripts/portfolio_snapshot.py --input txs.csv --dividends --date 2026-08-04
   ```
   Use `--div-tax-rate <decimal>` to override the default 30% withholding, e.g.
   `--div-tax-rate 0.20`.
3. **Present a per-symbol x per-date table** with these columns:
   `Date | Ticker | Shares | Div/share | Div $ | Div after tax`

   | Date | Ticker | Shares | Div/share | Div $ | Div after tax |
   |---|---|---|---|---|---|
   | 2024-02-09 | GOOGL | 45 | 0.2000 | 9.00 | 6.30 |
   | 2025-06-16 | META | 44 | 0.5250 | 23.10 | 16.17 |
   | TOTAL | | | | 32.10 | 22.47 |

   - `Shares` = post-split (canonical) shares held on the ex-date — same
     convention as `portfolio-snapshot`, so it is comparable across splits.
   - `Div/share` = the split-adjusted per-share dividend (yfinance amount,
     already scaled to current share terms).
   - `Div $` (pre-tax) = Shares × Div/share = actual dollars received.
   - `Div (after tax)` = `Div $` × (1 − dividend tax rate), default 30%.

   **Explain** that dividends received are separate from realized/unrealized
   capital gains and that the 30% is a withholding tax assumption (adjustable
   via `--div-tax-rate`). Optionally also report the running portfolio total
   (the `DIVIDENDS EARNED` TOTAL row).

## Data source

Dividend history is read from the local `corporate_actions` table (splits +
dividends, populated by `scraper.py --corporate-actions`) via DuckDB
`iceberg_scan` (active snapshot only — raw parquet globs would double-count
stale Iceberg snapshots). Falls back to a live yfinance fetch for any ticker
not yet ingested.