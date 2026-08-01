---
name: range-trading-analysis
description: >
  Use when the user asks to find the best price range to trade a stock (AAPL, META, TSLA, etc.)
  using DuckDB on the local stock_scraper dataset. Covers range-width optimization,
  sequential non-overlapping trade simulation, entry-within-pct constraint, and result display.
  DO NOT use for general stock questions or financial advice outside this specific analysis.
---

# Range Trading Analysis

Analyzes the local stock_scraper dataset (Iceberg/Parquet, queried via DuckDB)
to find the optimal entry/exit range that maximizes completed sequential trades.

## Where data lives

```
data/stocks/ohlcv_daily/data/symbol=<TICKER>/*.parquet
```

## Workflow

1. **Get the data** via DuckDB:
   ```bash
   .venv/bin/python -c "
   import duckdb
   con = duckdb.connect()
    rows = con.execute('''SELECT date, open, high, low, close
      FROM read_parquet('data/stocks/ohlcv_daily/data/symbol=<TICKER>/*.parquet')
     ORDER BY date''').fetchdf().to_dict('records')
   "
   ```

2. **Simulation logic** (each candidate entry/exit pair):
   - Scan rows in date order
   - When **not in a trade**: if `prev_close >= entry AND low <= entry` → buy at entry
   - When **in a trade**: if `high >= exit` → sell at exit, record trade
   - Continue from the next row after sell

3. **Grid search**:
   - Entry candidates: `current_price * (1 ± 0.10)`, step $0.25–$0.50
   - Width candidates: 5.1% to 25%, step 0.1%–0.25%
   - Pick the pair with the most completed trades

4. **Display results** in a table:
   ```
   Buy Date | Buy Low | Sell Date | Sell High | Days Held | P/L per share
   ```
   With a total P/L row at the bottom.

## Running

The `scripts/range_trade_analysis.py` script
has the full implementation. Run it with:
```bash
.venv/bin/python scripts/range_trade_analysis.py <TICKER> [--top N] [--widths 0.05,0.10] [--detail]
```

- `<TICKER>`: the symbol to analyze (e.g. `DIS`, `META`), optional, defaults to `META`.
- `--top N`: show the top N best ranges for each width.
- `--widths`: comma-separated widths to analyze, defaults to `0.05,0.10`.
- `--detail`: additionally print full trade-by-trade logs.

## Output format

Always display results as **one separate table per width** (never combine
widths into a single table). Example invocation and expected layout:

```bash
.venv/bin/python scripts/range_trade_analysis.py DIS --top 5
```

```
########## Width 5.0% ##########

Rank   Entry      Exit       Trades   Total P/L
----------------------------------------------
#1     $100.57   $105.60   27       $135.81
#2     $92.57    $97.20    25       $115.75
...

########## Width 10.0% ##########

Rank   Entry      Exit       Trades   Total P/L
----------------------------------------------
#1     $92.57    $101.83   16       $148.16
...
```

Summary columns: Rank, Entry, Exit, Trades (completed), Total P/L per share.
Note: fewer than N rows may appear if distinct price zones (entries ≥5% apart)
are limited for the symbol.
