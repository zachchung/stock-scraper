# Stock Scraper — US Stock OHLCV MCP Server

Expose US stock OHLCV data via an MCP server to Claude Desktop, enabling queries like:

> *"Find the best range to trade TSLA with an entry within 20% of the current price to maximize the number of completed trades over the last 3 years."*

---

## Architecture

```
[ Public Web API ]
        │
        ▼ (1) Python/PySpark — runs natively on your Mac
 [ Local RAM / Memory ]
        │
        ▼ (2) Saves directly to your SSD
 ┌───────────────────────────────────────────────┐
 │  YOUR MAC STORAGE                             │
 │                                                │
 │  ~/code/stock_scraper/data/                    │
 │   └── [ Parquet Data Files & Iceberg Logs ]   │
 └───────────────────────┬───────────────────────┘
                         │ (3) DuckDB reads directly
 ┌───────────────────────▼───────────────────────┐
 │  FastMCP Server (runs natively on your Mac)   │
 │  - Bundles DuckDB engine                      │
 │  - Exposes tools/resources over stdio         │
 └───────────────────────▲───────────────────────┘
                         │
                         │ (4) Stdio stream
 ┌───────────────────────┴───────────────────────┐
 │  CLAUDE DESKTOP APP                           │
 └───────────────────────────────────────────────┘
```

### Components

| Layer | Technology | Role |
|-------|-----------|------|
| **Data Ingestion** | Python + PySpark | Fetch OHLCV data from public web API, transform |
| **Storage** | Apache Iceberg (Parquet) on local SSD | Durable, queryable historical data |
| **Query Engine** | DuckDB (bundled in FastMCP) | Read Parquet/Iceberg files directly |
| **MCP Server** | FastMCP (Python) | Expose DuckDB queries as MCP tools/resources |
| **Client** | Claude Desktop App | Consume MCP tools via stdio |

### Data Flow

1. A Python/PySpark script fetches OHLCV data from **yfinance** (Yahoo Finance) or **investing.com**.
2. Data is processed in-memory on your Mac then persisted to SSD as Apache Iceberg tables backed by Parquet files.
3. The FastMCP server runs natively on your Mac, uses DuckDB to query the Parquet files directly.
4. Claude Desktop connects to the FastMCP server via stdio and issues natural-language trading queries.

---

## Implementation Steps

### Step 1: Data Ingestion Script

[`scraper.py`](src/stock_scraper/scraper.py) fetches daily and intraday OHLCV for **all S&P 500 constituents** + **VOO** from **yfinance**, processes it with PySpark, and stores into Iceberg tables partitioned by symbol under `data/stocks/`. It supports two modes:

- **Daily** (`--daily --years 5`): full historical download for daily OHLCV.
- **Intraday** (`--intraday --interval 1h --years 2`): historical download for intraday bars.
- **Incremental** (`--incremental`): detects the latest date/timestamp per symbol and fetches only rows after that date. Works with both `--daily` and `--intraday`.

### Known issue & fix — stale daily bar on incremental load

Bug: If `--daily` runs mid-session, the most recent day's bar is ingested from yfinance while it is still forming (partial OHLC), so only half a day of volume/range gets stored. The next incremental run uses that same date and fetches only rows after it (`last_date + 1`), so the partial bar was never updated and remained stale forever. Example: AAPL `2026-08-04` was stored at ~25.6M volume vs ~68M in the final bar.

Solution:
1. Incremental fetch now re-fetches from `last_date - 5d` instead of `last_date + 1d`, so the last stored bar is always updated.
2. The daily MERGE now uses `WHEN MATCHED THEN UPDATE SET *` — so any re-fetched recent bar overwrites the stale one. This alone fixes the full (non-incremental) load, since `period=max` already re-downloads/updates the trailing days.

### Step 2: Warehouse

A local Apache Iceberg catalog is initialized at `data/` with table `local.stocks.ohlcv_daily` (schema: `symbol`, `date`, `open`, `high`, `low`, `close`, `volume`, `source`). Historical data has already been backfilled; run `scraper.py --daily --incremental` daily to refresh with only new rows.

### Step 3: FastMCP + DuckDB Server

[`mcp_server.py`](src/stock_scraper/mcp_server.py) runs a FastMCP server that bundles DuckDB and exposes:

- **`query(sql)`** — run arbitrary DuckDB SQL against the warehouse
- **`get_stock_data(symbol, limit)`** — OHLCV for a ticker
- **`list_symbols()`** — all available tickers
- **`analyze_trades(symbol, entry_range_pct, years)`** — find optimal trade ranges

Run it directly:

```bash
source .venv/bin/activate
STOCK_DATA_DIR=~/code/stock_scraper/data python src/stock_scraper/mcp_server.py
```

### Step 4: Claude Desktop Configuration

Edit `claude_desktop_config.json` to register the MCP server (adjust paths to match your setup):

```json
{
  "mcpServers": {
    "stock-scraper": {
      "command": "/Users/ZacharyChung1/code/stock_scraper/.venv/bin/python",
      "args": ["/Users/ZacharyChung1/code/stock_scraper/src/stock_scraper/mcp_server.py"],
      "env": {
        "STOCK_DATA_DIR": "/Users/ZacharyChung1/code/stock_scraper/data"
      }
    }
  }
}
```

### Step 5: Iterate & Refine

- Test with sample queries
- Optimize DuckDB query performance (partition pruning, sorting)
- Add caching layer if needed
- Extend to support intraday data, corporate actions, etc.

---

## Example Queries

Once the MCP server is running, Claude Desktop can answer:

- "Find the best price range to trade AAPL where the range width is greater than 5%, entry is within 10% of the current price, and completed trades are maximized over the last 10 years. Trades must be sequential and non-overlapping: buy when the stock crosses down through the entry price, then sell when it reaches the exit price. The next trade starts only after the previous one closes. List all occurrences with dates. Show result in a table with columns: Buy Date/Buy Low/Sell Date/Sell High/Days Held. And show the total $ P/L per share per trade in a new line."
- "What is the average daily range for AAPL over the last 6 months?"
- "List all recent drawdowns of MU in the last 3 years." (drawdown risk)
- "Rank months based on VOO performance." (time-period analysis)
- "When MU opens down 10%, does it normally extend losses intraday or recover? Show percentage of each."
- "After Amazon earnings beat, does the price momentum follow and for how many days?"
- "What is the historical daily P/E ratio for this stock?"
- "If today's volume of MU > 134% of the 10-day average volume, buy at market close and sell 2 trading days after — what's the average P/L of this strategy?"

---

## Installation

### 1. Java JDK

PySpark runs on the JVM. Install OpenJDK 17 via Homebrew:

```bash
brew install openjdk@17
```

Add to your shell config (`~/.zshrc`):

```bash
export JAVA_HOME=$HOME/.sdkman/candidates/java/current
```

### 2. Python + PySpark + Iceberg

```bash
# Create virtual environment
python -m venv .venv && source .venv/bin/activate

# Install PySpark (bundles Spark binaries)
pip install pyspark

# Install Iceberg — you'll need the Spark runtime jar:
# Download from https://iceberg.apache.org/releases/
# Then add it to your Spark session config:
#   spark.jars = /path/to/iceberg-spark-runtime-3.5_2.12-1.6.0.jar
```

### 3. DuckDB

Install DuckDB CLI for ad-hoc queries:

```bash
brew install duckdb
```

DuckDB is also bundled in the MCP server via the Python `duckdb` package.

---

## Resource Usage

Approximate disk footprint:

| Component | Size |
|-----------|------|
| OpenJDK 17 (`brew install openjdk@17`) | ~350 MB |
| PySpark (`pip install pyspark`) | ~250 MB |
| Iceberg Spark runtime jar | ~80 MB |
| **Total** | **~680 MB** |

### Other concerns

- **Cold start**: PySpark/Java takes ~15–30s to spin up for each scraper run. Since the scraper runs once a day (or on demand), this is negligible.
- **Iceberg metadata**: Creates small extra files per table; overhead is < 1 MB.

---

## Development Setup

```bash
# 1. Create and activate a Python virtual environment
python -m venv .venv && source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run data ingestion
python src/stock_scraper/scraper.py --daily --years 5               # initial daily backfill
python src/stock_scraper/scraper.py --daily --incremental           # daily refresh
python src/stock_scraper/scraper.py --intraday --interval 1h --years 2  # intraday backfill
python src/stock_scraper/scraper.py --intraday --incremental        # intraday refresh

# 4. Start MCP server
STOCK_DATA_DIR=~/code/stock_scraper/data python src/stock_scraper/mcp_server.py

# 5. Register with Claude Desktop (see Step 4)
```

---

## Requirements

- Python 3.11+
- OpenJDK 17
- PySpark (runs natively on Mac — no cluster needed)
- Apache Iceberg + Parquet (via PySpark)
- DuckDB

---

## What You'll Learn

- **Distributed data processing concepts** — PySpark DataFrames, partitioning, lazy evaluation
- **Open table formats** — Apache Iceberg catalogs, snapshots, time-travel queries
- **Columnar storage** — Parquet file format, compression, predicate pushdown
- **Local analytics** — DuckDB querying Parquet/Iceberg files directly
- **MCP protocol** — stdio-based server design, tool/resource/prompt exposure

This project touches the modern data stack — Spark → Iceberg → Parquet → DuckDB — all on a single Mac. Solid portfolio material.

---

## Analyst Price Targets Extension

### Motivation

Ingest analyst price targets so the MCP server can answer questions like:

> *"What's the average analyst price target vs current price?"*
>
> *"How has the consensus target for AAPL changed over the last 6 months?"*
>
> *"Which analysts have upgraded/downgraded META recently, and what were their price targets?"*

### Data Sources

All data comes from **yfinance** — the same library already used for OHLCV:

| Data | yfinance API | Description |
|------|-------------|-------------|
| Consensus Targets | `Ticker.info` (currentPrice, targetHighPrice, targetLowPrice, targetMeanPrice, targetMedianPrice) | Current consensus snapshot per symbol |
| Upgrades/Downgrades | `Ticker.upgrades_downgrades` | Historical individual analyst actions with price targets |

### New Tables

**Table: `local.stocks.analyst_targets`** (Iceberg)

| Column | Type | Description |
|--------|------|-------------|
| `symbol` | `STRING` | Ticker |
| `current_price` | `DOUBLE` | Current stock price at fetch time |
| `target_high` | `DOUBLE` | Highest analyst target |
| `target_low` | `DOUBLE` | Lowest analyst target |
| `target_mean` | `DOUBLE` | Mean (average) analyst target |
| `target_median` | `DOUBLE` | Median analyst target |
| `recommendation_mean` | `DOUBLE` | Mean recommendation (1=strong buy, 5=strong sell) |
| `recommendation_key` | `STRING` | Text recommendation (buy, hold, etc.) |
| `num_analysts` | `INT` | Number of analysts covering |
| `fetched_at` | `STRING` | ISO-8601 timestamp of this snapshot |

Each `--analyst` run **appends** a new row per symbol, building a historical consensus time series.

**Table: `local.stocks.analyst_upgrades_downgrades`** (Iceberg)

| Column | Type | Description |
|--------|------|-------------|
| `symbol` | `STRING` | Ticker |
| `grade_date` | `STRING` | ISO-8601 timestamp of action |
| `firm` | `STRING` | Analyst firm name |
| `to_grade` | `STRING` | New rating (Buy, Hold, Sell, etc.) |
| `from_grade` | `STRING` | Previous rating |
| `action` | `STRING` | `main`, `up`, `down`, `reit`, `init` |
| `price_target` | `DOUBLE` | New price target |
| `prior_price_target` | `DOUBLE` | Previous price target |

### Ingestion

```bash
# Consensus targets only (fast — appends a snapshot row per symbol)
python src/stock_scraper/scraper.py --targets

# Full: targets + upgrades/downgrades for all S&P 500
python src/stock_scraper/scraper.py --analyst

# Specific symbols (either flag works)
python src/stock_scraper/scraper.py --targets --tickers AAPL MSFT META
python src/stock_scraper/scraper.py --analyst --tickers AAPL MSFT META
```

### MCP Tools

```python
get_analyst_targets(symbol: str) -> str
  """Latest consensus price targets for a ticker with upside/downside %."""

analyst_targets_history(symbol: str, n: int = 20) -> str
  """Historical consensus snapshots over time, one row per ingestion run."""

analyst_target_history(symbol: str, limit: int = 50) -> str
  """Historical individual analyst price target changes."""

analyst_consensus_summary() -> str
  """Aggregate summary across all symbols: avg mean upside, analyst coverage, etc."""
```

Once ingested, Claude can answer:

- "What is the average analyst price target for AAPL vs its current price? Show the upside/downside percentages."
- "How has the mean analyst target for TSLA changed over the past year? Show me the time series from analyst_targets."
- "Which analysts recently upgraded AMZN and to what price target?"
- "What's the overall market sentiment? Show me the analyst consensus summary across all S&P 500 stocks."
- "Compare AAPL's current analyst target mean upside to its historical forward returns." (joins with ohlcv)
- "Find stocks where the current price is above the mean analyst target (negative upside)."
- "For GOOGL, show current analyst consensus targets vs current price, and compare against historical accuracy: how often did past price targets get reached within 180 days, and what was the actual best/worst forward return?" (joins analyst_upgrades_downgrades with ohlcv)

### Example Outputs

**Current consensus vs current price:**

```
 AAPL analyst targets (latest):
┌────────┬────────────┬───────────┬──────────┬────────────┬──────────────┬─────────────────┬─────────────┐
│ symbol │ cur_price  │ tgt_high  │ tgt_low  │ tgt_mean   │ mean_upside% │ num_analysts    │ reco        │
├────────┼────────────┼───────────┼──────────┼────────────┼──────────────┼─────────────────┼─────────────┤
│ AAPL   │     340.08 │    400.00 │   215.00 │    319.72  │        -5.99 │              43 │ buy         │
└────────┴────────────┴───────────┴──────────┴────────────┴──────────────┴─────────────────┴─────────────┘
```

**Historical accuracy: did past targets get hit?**

```
GOOGL analyst accuracy (688 past actions):
  avg target upside: +361.4%   (many targets set years ago at much lower prices)
  avg actual best 180d: +33.3%
  avg actual worst 180d: -13.9%
  targets hit within 180d: 63.8% of the time
```

## EPS Estimates & Fundamentals Extension

### Motivation

Support PE ratio analysis (TTM, last-fiscal-year, forward) and valuation snapshots.

### Data Sources

All data comes from **yfinance**:

| Data | yfinance API | Description |
|------|-------------|-------------|
| Fiscal labels | `Ticker.info` (lastFiscalYearEnd / nextFiscalYearEnd) | fiscal_period_end / fiscal_quarter / fiscal_year per earnings report |
| EPS Estimates | `Ticker.earnings_estimate` | Consensus EPS for current/next quarter (0q/+1q) and current/next fiscal year (0y/+1y) |
| Fundamentals | `Ticker.info` | Market cap, 52-week high/low, profit margin, shares outstanding, EPS TTM/current-year/forward |

### New/Changed Tables

**Table: `local.stocks.earnings_dates`** (changed — added fiscal columns)

| Column | Type | Description |
|--------|------|-------------|
| `symbol` | `STRING` | Ticker |
| `report_date` | `STRING` | ISO-8601 earnings announcement timestamp |
| `eps_estimate` | `DOUBLE` | Consensus estimate for that quarter |
| `eps_actual` | `DOUBLE` | Reported EPS |
| `surprise_pct` | `DOUBLE` | Actual vs estimate surprise % |
| `market_session` | `STRING` | pre_market / during_market / post_market |
| `fiscal_period_end` | `DATE` | Fiscal quarter end this report covers |
| `fiscal_quarter` | `INT` | Fiscal quarter (1–4) |
| `fiscal_year` | `INT` | Fiscal year |

**Table: `local.stocks.eps_estimates`** (new — append snapshot per run, long format)

| Column | Type | Description |
|--------|------|-------------|
| `symbol` | `STRING` | Ticker |
| `fetched_at` | `STRING` | ISO-8601 timestamp of this snapshot |
| `period` | `STRING` | `0q` / `+1q` / `0y` / `+1y` |
| `period_label` | `STRING` | current_quarter / next_quarter / current_year / next_year |
| `eps_avg` | `DOUBLE` | Consensus average EPS |
| `eps_low` | `DOUBLE` | Consensus low EPS |
| `eps_high` | `DOUBLE` | Consensus high EPS |
| `num_analysts` | `INT` | Number of analysts covering |

**Table: `local.stocks.fundamentals_snapshot`** (new — append snapshot per run)

| Column | Type | Description |
|--------|------|-------------|
| `symbol` | `STRING` | Ticker |
| `fetched_at` | `STRING` | ISO-8601 timestamp of this snapshot |
| `market_cap` | `DOUBLE` | Market capitalization |
| `fifty_two_week_high` | `DOUBLE` | 52-week high |
| `fifty_two_week_low` | `DOUBLE` | 52-week low |
| `all_time_high` | `DOUBLE` | All-time high |
| `all_time_low` | `DOUBLE` | All-time low |
| `profit_margin` | `DOUBLE` | Net profit margin |
| `shares_outstanding` | `DOUBLE` | Shares outstanding |
| `eps_ttm` | `DOUBLE` | Trailing twelve-month EPS |
| `eps_current_year` | `DOUBLE` | Current fiscal year consensus EPS |
| `forward_eps` | `DOUBLE` | Next fiscal year consensus EPS |
| `trailing_pe` | `DOUBLE` | Trailing P/E (vendor-computed) |
| `forward_pe` | `DOUBLE` | Forward P/E (vendor-computed) |
| `price_to_book` | `DOUBLE` | Price-to-book ratio |
| `book_value` | `DOUBLE` | Book value per share |
| `current_ratio` | `DOUBLE` | Current ratio (liquidity) |
| `quick_ratio` | `DOUBLE` | Quick ratio (liquidity) |
| `debt_to_equity` | `DOUBLE` | Debt-to-equity ratio |
| `return_on_equity` | `DOUBLE` | Return on equity |
| `last_fiscal_year_end` | `DATE` | Prior fiscal year end |
| `next_fiscal_year_end` | `DATE` | Current fiscal year end |

### Ingestion

```bash
# Earnings dates + income statements + EPS estimates
python src/stock_scraper/scraper.py --earnings --tickers AAPL MSFT META

# Quarterly cash flow statements
python src/stock_scraper/scraper.py --cashflow --tickers AAPL MSFT META

# Quarterly balance sheets
python src/stock_scraper/scraper.py --balancesheet --tickers AAPL MSFT META

# Fundamentals snapshot (market cap, 52w high/low, profit margin)
python src/stock_scraper/scraper.py --fundamentals --tickers AAPL MSFT META

# Macro data: S&P/Nasdaq/Dow/Russell, VIX, Treasury yields, BTC/ETH, gold, oil
python src/stock_scraper/scraper.py --macro --period 10y
```

## Income Statement History Backfill (SEC EDGAR)

### Motivation

yfinance only exposes ~5 years of annual income statement history. SEC EDGAR's
XBRL `companyfacts` API has the full as-reported history (back to the 1990s for
most US filers). `scripts/edgar_income.py` backfills older annual periods into
the existing `income_statements` table — same schema, same `frequency='annual'`
rows — so long-term EPS/revenue/margin analysis works locally.

### How it works

- Resolves ticker → CIK via SEC `company_tickers.json`, then fetches
  `data.sec.gov/api/xbrl/companyfacts/CIK##########.json`.
- Maps XBRL tags to the `income_statements` columns: `Revenues`/`SalesRevenueNet`
  → `total_revenue`, `GrossProfit`, `OperatingIncomeLoss`, `NetIncomeLoss`,
  `EarningsPerShareDiluted`, and computes `ebitda = operating income +
  depreciation/amortization`.
- Keeps only annual periods (10-K/20-F, ~360-day span) from the latest filing
  per fiscal year (max accession number), normalizing fiscal dates to month-end
  to match yfinance's convention.
- Merges into `local.stocks.income_statements` via `scraper.write_income_to_iceberg`.

By default it only backfills periods **not already present** (recent yfinance
rows are left untouched). Use `--update` to also refresh existing rows from
EDGAR (authoritative, includes restatements).

```bash
# Backfill older annual history for specific tickers
python scripts/edgar_income.py AAPL MSFT GOOGL

# Only load fiscal years up to a cutoff (leave the recent ones from yfinance)
python scripts/edgar_income.py AAPL --max-year 2020

# Full refresh including already-present rows
python scripts/edgar_income.py AAPL --update
```

The `--earnings` flag of `scraper.py` now also runs the EDGAR backfill
automatically for each ticker, so `income_statements` always carries the full
history — no separate step needed. It is idempotent (inserts only missing
annual periods) and skips the EDGAR fetch entirely for symbols that already
have long history (oldest annual period > 8 years ago).

### Annual EPS growth (split-adjusted)

EDGAR/yfinance restate historical EPS on mixed split bases (some years pre-split,
some post-split), which creates fake YoY drops at split boundaries (e.g. AAPL
shows FY11 EPS $27.68 then FY12 $6.31 — a 7:1 split basis change, not a decline).
`scripts/split_adjust.py` detects each year's basis via implied shares
(net_income / diluted_eps) and normalizes everything to the current basis.

```bash
# Annual EPS growth with split normalization for any tickers
python scripts/eps_growth.py AAPL MSFT NVDA

# Change the CAGR window
python scripts/eps_growth.py AAPL --years 10

# Also draw a terminal bar chart of annual EPS (█ positive, ▒ loss year)
python scripts/eps_growth.py AAPL --chart
```

`scripts/stock_research.py` applies the same normalization to the annual EPS
table and CAGR in its research snapshot.

## Iceberg snapshot maintenance
Every ingestion run creates a new snapshot; old data files stay on disk until
snapshots are expired, which can cause duplicate rows when reading via raw
parquet globs. Query tables with `iceberg_scan(...)` (active snapshot only), and
expire old snapshots to reclaim disk:

```bash
python -c "
import sys; sys.path.insert(0, 'src')
from stock_scraper.scraper import get_spark
spark = get_spark()
from datetime import datetime, timezone
now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
spark.sql(f\"CALL local.system.expire_snapshots('stocks.ohlcv_daily', older_than => TIMESTAMP '{now}', retain_last => 1)\")
"
```

### Example Queries

- "Compute TTM, last-fiscal-year, and forward PE for AAPL." (joins ohlcv + earnings_dates + eps_estimates)
- "What's AAPL's market cap, 52-week high/low, and profit margin?"
- "How has the forward EPS estimate for NVDA changed over the last month?" (eps_estimates history)

## Corporate Actions Extension (Splits & Dividends)

### Motivation

Portfolio PnL needs split history (to reconcile share counts across split bases) and dividend history (for realized income). Both were previously fetched from yfinance at query time; now they're ingested once into a local warehouse table.

### New Table

**Table: `local.stocks.corporate_actions`** (Iceberg, constant `CORPORATE_ACTIONS_TABLE`)

| Column | Type | Description |
|--------|------|-------------|
| `symbol` | `STRING` | Ticker |
| `date` | `DATE` | Split effective date / dividend ex-date |
| `action_type` | `STRING` | `split` or `dividend` |
| `split_factor` | `DOUBLE` | New shares per 1 old share (splits only; `NULL` for dividends) |
| `amount` | `DOUBLE` | Per-share dividend (dividends only; `NULL` for splits) |

**Uniqueness:** `(symbol, action_type, date)` — a split and a dividend can share a date without colliding.

### Ingestion

```bash
# All S&P 500 + VOO
python src/stock_scraper/scraper.py --corporate-actions

# Specific symbols
python src/stock_scraper/scraper.py --corporate-actions --tickers AAPL GOOGL META
```

The MERGE upserts on `(symbol, action_type, date)`, so re-runs are idempotent. `scripts/portfolio_snapshot.py` reads splits and dividends from this table via DuckDB (no network needed at report time), filtering on `action_type`. Dividend income is folded into realized PnL (cap gains + dividends per ticker; shown separately under `DIVIDENDS RECEIVED` in the snapshot). If a ticker isn't in the local table, the snapshot falls back to a live yfinance fetch for both splits and dividends.

## Planned: Intraday / Hourly OHLCV Data

### Motivation

Daily OHLCV data captures the open, high, low, and close for the full trading session, but it can't answer *when* during the day key price levels were hit. For example, the MU gap-up analysis found that MU hits a higher price intraday on **100% of gap-up days**, with an average best move of +2.89% from open — but with only daily data, there's no way to know if that peak occurred 5 minutes after open or 5 minutes before close.

Intraday data unlocks questions like:

> *"When MU gaps up +3% at open, does the intraday high typically occur within the first 30 minutes, or does the stock grind higher through the session?"*

> *"If I buy MU at the open on a gap-up day and set a 2% trailing stop, what percentage of days would I get stopped out before hitting the day's high?"*

> *"What's the optimal time of day to sell MU after a gap-up open to maximize average return?"*

### New Data Source

yfinance supports intraday intervals via `Ticker.history(period="1mo", interval="1h")`. The available intervals are:

| Interval | Max Period | Use Case |
|----------|-----------|----------|
| `1m` | 7 days | Ultra-short term |
| `2m` | 60 days | Short term |
| `5m` | 60 days | Short term |
| `15m` | 60 days | Swing trading |
| `30m` | 60 days | Swing trading |
| `60m` (`1h`) | 730 days (2 years) | **Primary — captures intraday structure** |
| `1d` | Max | Already ingested |

For most strategy analysis, **1-hour bars** offer the best balance: enough granularity to determine time-of-day patterns while keeping storage manageable (~1 GB for all S&P 500 over 2 years, compared to ~50 GB+ for 1-minute data).

### New Table

**Table: `local.stocks.ohlcv_intraday`** (Iceberg, constant `OHLCV_TABLE_INTRADAY`)

| Column | Type | Description |
|--------|------|-------------|
| `symbol` | `STRING` | Ticker |
| `timestamp` | `TIMESTAMP` | Start of the bar (America/New_York) |
| `open` | `DOUBLE` | Open price |
| `high` | `DOUBLE` | High price |
| `low` | `DOUBLE` | Low price |
| `close` | `DOUBLE` | Close price |
| `volume` | `BIGINT` | Volume |
| `interval` | `STRING` | Bar size (`1h`, `30m`, etc.) |

**Partitioning:** `(symbol, year, month)` — same scheme as the daily table, with an additional `interval` filter.

### Ingestion

The `--intraday` flag added to `scraper.py` supports two modes:

- **Full fetch** (`--intraday --years 2`): downloads the full available history for the given interval.
- **Incremental** (`--intraday --incremental`): detects the latest timestamp already stored per symbol (via DuckDB) and fetches only rows after that date. Falls back to a full fetch for any symbol with no local data. The `MERGE INTO` upsert prevents duplicates on `(symbol, timestamp, interval)`.

Pre/post market data is excluded by default (yfinance extended-hours data is unreliable). To include it, add `--prepost`.

```bash
# Full fetch — 2 years of 1-hour bars for all S&P 500
python src/stock_scraper/scraper.py --intraday --interval 1h --years 2

# Incremental — fetch only new bars since last ingestion
python src/stock_scraper/scraper.py --intraday --interval 1h --incremental

# Specific symbol
python src/stock_scraper/scraper.py --intraday --tickers MU --interval 1h --years 1

# Include pre/post market data (unreliable — known yfinance artifacts)
python src/stock_scraper/scraper.py --intraday --prepost
```

Key design decisions:

- **Stores multiple intervals** in the same table, differentiated by the `interval` column.
- **Aligns bars to hour boundaries** — 10:00 AM bar runs 10:00–10:59 ET.

### MCP Tools (planned)

```python
get_intraday(symbol: str, interval: str = "1h", days: int = 60) -> str
  """Return intraday OHLCV bars for a ticker."""

intraday_pattern(symbol: str, condition: str = "gap_up", interval: str = "1h") -> str
  """Analyze intraday price patterns. E.g.:
     - When does the intraday high typically occur on gap-up days?
     - What's the average time-to-peak after open?
     - How often does the close retrace more than 50% of the intraday range?
  """
```

### Example Analysis

With 1-hour bars, the MU gap-up question becomes answerable:

```
MU gap-up days — average time to intraday peak (1h bars):
  Hour 0 (9:30-10:30): high reached 5/22 days (23%)
  Hour 1 (10:30-11:30): high reached 8/22 days (36%)
  Hour 2 (11:30-12:30): high reached 4/22 days (18%)
  Hour 3 (12:30-13:30): high reached 3/22 days (14%)
  Hour 4 (13:30-14:30): high reached 1/22 days (5%)
  Hour 5 (14:30-15:30): high reached 1/22 days (5%)
  Hour 6 (15:30-16:00): high reached 0/22 days (0%)

  Peak concentration: 59% of days hit the intraday high within the first 2 hours.
```

### Storage Estimates (S&P 500, 2 years of 1h bars)

| Component | Size |
|-----------|------|
| 1h bars (~6.5 bars/day × ~500 symbols × ~504 trading days) | ~1.6M rows, ~60 MB |
| Iceberg metadata | ~5 MB |
| **Total** | **~65 MB** |

### Status

Implemented. Supports full fetch and incremental load (regular hours only by default):

```bash
# Full fetch — 2 years of 1-hour bars for all S&P 500
python src/stock_scraper/scraper.py --intraday --interval 1h --years 2

# Incremental — fetch only new bars since last ingestion
python src/stock_scraper/scraper.py --intraday --interval 1h --incremental
```

The MCP server exposes two new tools — `get_intraday` (fetch bars) and `intraday_pattern` (analyze time-of-day patterns like gap-up peak distribution).

---

## Design Decisions

### Why no Docker?

Docker Desktop on macOS is heavy: a ~2–3 GB install plus a Linux VM consuming 1–2 GB RAM at all times, even when idle. For a single-user local project, this overhead isn't justified.

The MCP server runs natively as a Python process — no isolation needed since all services live on the same machine. The trade-off is environment dependency (Python + package versions), but a virtual environment handles that cleanly.

---

## Notes

- All data stays local — no cloud egress costs.
- DuckDB reads Parquet files directly, so queries are fast without a full database setup.
- The Iceberg layer enables schema evolution, time-travel queries, and transactional writes during ingestion.
