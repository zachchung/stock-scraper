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

[`scraper.py`](src/stock_scraper/scraper.py) fetches daily OHLCV for **all S&P 500 constituents** + **VOO** from **yfinance**, processes it with PySpark, and stores into Iceberg tables partitioned by symbol under `data/stocks/`. It supports both backfill (`--backfill --years 5`) and incremental append (merges on `symbol` + `date`).

### Step 2: Warehouse

A local Apache Iceberg catalog is initialized at `data/` with the schema (`symbol`, `date`, `open`, `high`, `low`, `close`, `volume`, `source`). Historical data has already been backfilled; run `scraper.py` again to refresh.

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

# 3. Run data ingestion (backfill)
python src/stock_scraper/scraper.py --backfill --years 5

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

## Design Decisions

### Why no Docker?

Docker Desktop on macOS is heavy: a ~2–3 GB install plus a Linux VM consuming 1–2 GB RAM at all times, even when idle. For a single-user local project, this overhead isn't justified.

The MCP server runs natively as a Python process — no isolation needed since all services live on the same machine. The trade-off is environment dependency (Python + package versions), but a virtual environment handles that cleanly.

---

## Notes

- All data stays local — no cloud egress costs.
- DuckDB reads Parquet files directly, so queries are fast without a full database setup.
- The Iceberg layer enables schema evolution, time-travel queries, and transactional writes during ingestion.
