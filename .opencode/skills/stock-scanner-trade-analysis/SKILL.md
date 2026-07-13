---
name: stock-scanner-trade-analysis
description: >
  Use when the user asks to find the best price range to trade a stock (AAPL, META, TSLA, etc.)
  using DuckDB on the local stock_scraper dataset. Covers range-width optimization,
  sequential non-overlapping trade simulation, entry-within-pct constraint, and result display.
  DO NOT use for general stock questions or financial advice outside this specific analysis.
---

# Stock Scanner — Trade Range Analysis

Analyzes the local stock_scraper dataset (Iceberg/Parquet, queried via DuckDB)
to find the optimal entry/exit range that maximizes completed sequential trades.

## Where data lives

```
data/stocks/ohlcv/data/symbol=<TICKER>/*.parquet
```

## Workflow

1. **Get the data** via DuckDB:
   ```bash
   .venv/bin/python -c "
   import duckdb
   con = duckdb.connect()
   rows = con.execute('''SELECT date, open, high, low, close
     FROM read_parquet('data/stocks/ohlcv/data/symbol=<TICKER>/*.parquet')
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

The `trade_analysis.py` script in the project root
has the full implementation. Run it with:
```bash
.venv/bin/python trade_analysis.py
```

To analyze a different ticker, edit the `symbol=META` path in the DuckDB query.
