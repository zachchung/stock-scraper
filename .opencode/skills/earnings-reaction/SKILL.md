---
name: earnings-reaction
description: >
  Use when the user asks about post-earnings day reactions for a stock (JPM,
  MSFT, AAPL, GOOGL, etc.) using the local stock_scraper dataset and yfinance
  for earnings data. Covers pre-market and post-market earnings release handling,
  EPS surprise vs price reaction comparison, and summary statistics.
  DO NOT use for general stock questions or financial advice outside this
  specific analysis.
---

# Earnings Reaction Analysis

Analyzes post-earnings day price reactions by combining yfinance earnings data
(EPS estimate, actual, surprise) with the local OHLCV dataset (Iceberg/Parquet,
queried via DuckDB) to show gap % and day return % for each earnings event.

## Where data lives

```
data/stocks/ohlcv/data/symbol=<TICKER>/*.parquet
```

Earnings dates come live from yfinance (`yf.Ticker(<TICKER>).earnings_dates`).

## Release handling

- **Post-market** (MSFT, AAPL, GOOGL, etc.): earnings drop after 4PM ET.
  Reaction date = next trading day. Gap% = next open vs pre-earnings close.
- **Pre-market** (JPM, BAC, etc.): earnings drop before 9:30AM ET.
  Reaction date = same trading day. Gap% = same-day open vs previous close.

## Workflow

1. **Fetch earnings dates** via yfinance for the given symbol.
2. **Query OHLCV** via DuckDB to get prices around each earnings date.
3. **Build table** with columns:
   `EarningDate | Release | ReactionDate | EPS Est | EPS Act | Surp% | Gap% | Day Ret%`
4. **Print summary** line with positive/negative count and max up/down moves.

## Running

The `scripts/earnings_reaction.py` script has the full implementation:

```bash
.venv/bin/python scripts/earnings_reaction.py <SYMBOL> [N]
```

- `<SYMBOL>` — ticker (e.g., JPM, MSFT, AAPL, GOOGL)
- `[N]` — number of past earnings to show (default 10)
