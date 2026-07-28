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

- **Backfill** (`--backfill --years 5`): full historical download for the given year window.
- **Incremental** (`--incremental`): detects the latest date/timestamp already stored per symbol (via DuckDB) and fetches only rows after that date. Falls back to a full fetch for any symbol with no local data. Works for both daily (`--incremental`) and intraday (`--intraday --incremental`).

### Step 2: Warehouse

A local Apache Iceberg catalog is initialized at `data/` with the schema (`symbol`, `date`, `open`, `high`, `low`, `close`, `volume`, `source`). Historical data has already been backfilled; run `scraper.py --incremental` daily to refresh with only new rows.

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
python src/stock_scraper/scraper.py --backfill --years 5   # initial backfill
python src/stock_scraper/scraper.py --incremental           # daily refresh

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

## Planned: Earnings Data Extension

### Motivation

Ingest earnings announcement dates and income statement data so the MCP server can answer questions like:

> *"Show me a table listing the past 10 MSFT post-earnings day reactions."*

### New Data Source

All data comes from **yfinance** — the same library already used for OHLCV:

| Data | yfinance API | Frequency | Rows |
|------|-------------|-----------|------|
| Earnings Dates | `Ticker.earnings_dates` | Quarterly | ~20K (500 symbols × 40 quarters) |
| Income Statements | `Ticker.quarterly_income_stmt` | Quarterly | ~20K |

### Pre/Post Market Detection

The `earnings_dates` index is a timezone-aware datetime (`America/New_York`), so the release session can be determined:

| Timestamp (ET) | Market Session | Reaction Day |
|---|---|---|
| Before 09:30 | `pre_market` | Same trading day |
| 09:30 – 16:00 | `during_market` | Same trading day |
| After 16:00 | `post_market` | **Next** trading day |

This is critical for measuring the correct price reaction — MSFT reports post-market at 16:00 ET, so its day-1 reaction is the next trading day's open-to-close return plus the overnight gap.

### New Tables

**Table: `local.stocks.earnings_dates`** (Iceberg)

| Column | Type | Description |
|--------|------|-------------|
| `symbol` | `STRING` | Ticker |
| `report_date` | `STRING` | Earnings release timestamp as ISO-8601 string (America/New_York) |
| `eps_estimate` | `DOUBLE` | Consensus EPS estimate |
| `eps_actual` | `DOUBLE` | Reported EPS |
| `surprise_pct` | `DOUBLE` | (actual - estimate) / \|estimate\| × 100 |
| `market_session` | `STRING` | `pre_market`, `during_market`, or `post_market` |

**Table: `local.stocks.income_statements`** (Iceberg)

| Column | Type | Description |
|--------|------|-------------|
| `symbol` | `STRING` | Ticker |
| `fiscal_date` | `DATE` | Quarter-end date |
| `total_revenue` | `DOUBLE` | Total revenue |
| `gross_profit` | `DOUBLE` | Gross profit |
| `operating_income` | `DOUBLE` | Operating income |
| `net_income` | `DOUBLE` | Net income |
| `diluted_eps` | `DOUBLE` | Diluted EPS |
| `ebit` | `DOUBLE` | EBIT |
| `ebitda` | `DOUBLE` | EBITDA |

### Ingestion

New CLI flag `--earnings` added to `scraper.py`:

```bash
python src/stock_scraper/scraper.py --earnings         # fetch earnings for all symbols
python src/stock_scraper/scraper.py --earnings --tickers AAPL MSFT  # specific symbols
```

Runs in the same ticker loop as OHLCV, writing to separate Iceberg tables via `MERGE INTO`.

### MCP Tools (planned)

```python
get_earnings(symbol: str, n: int = 10) -> str
  """Quarterly earnings dates with EPS surprise for a ticker."""

post_earnings_reaction(symbol: str, n: int = 10) -> str
  """Symbol's post-earnings day price reaction. Returns a table:
     report_date, market_session, eps_surprise%, first_trading_day,
     open, close, day1_return%, overnight_gap%."""
```

The `post_earnings_reaction` tool joins `earnings_dates` with the existing `ohlcv` table, using the `market_session` flag to pick the correct reaction day.

### Example Output

```
 MSFT post-earnings reactions (last 5):
┌──────────────┬───────────────┬──────────────┬────────────────┬────────┬────────┬──────────────┬─────────┐
│ report_date  │ market_session│ eps_surprise%│ reaction_date  │ open   │ close  │ day1_return% │ gap%    │
├──────────────┼───────────────┼──────────────┼────────────────┼────────┼────────┼──────────────┼─────────┤
│ 2026-04-29   │ post_market   │         4.90 │ 2026-04-30     │ 468.21 │ 472.54 │        +0.92 │   -0.30 │
│ 2026-01-28   │ post_market   │         5.69 │ 2026-01-29     │ 452.10 │ 458.30 │        +1.37 │   +0.50 │
│ 2025-10-29   │ post_market   │         1.23 │ 2025-10-30     │ 439.80 │ 441.15 │        +0.31 │   -0.10 │
│ 2025-07-29   │ post_market   │         6.80 │ 2025-07-30     │ 465.50 │ 470.20 │        +1.01 │   +0.80 │
│ 2025-04-29   │ post_market   │         3.15 │ 2025-04-30     │ 428.90 │ 425.40 │        -0.82 │   -0.60 │
└──────────────┴───────────────┴──────────────┴────────────────┴────────┴────────┴──────────────┴─────────┘
```

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

**Table: `local.stocks.ohlcv_intraday`** (Iceberg)

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
