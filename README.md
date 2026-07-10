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
 ┌──────────────────────────────────────────────────────┐
 │  YOUR MAC STORAGE                                    │
 │                                                       │
 │  ~/code/stock_scraper/data/                                         │
 │   └── [ Parquet Data Files & Iceberg Logs ]  ◄──┐     │
 └─────────────────────────────────────────────────│─────┘
                                                    │
 ┌─────────────────────────────────────────────────│─────┐
 │  DOCKER RUNTIME                                 │      │
 │  ┌──────────────────────────────────────────┐   │      │
 │  │ FastMCP Container                        │   │      │
 │  │ - Bundles DuckDB engine                  │───┘      │
 │  │ - Mounts /warehouse folder read-only     │ (3) Scans│
 │  └──────────────────────────────────────────┘          │
 └───────────────────────▲────────────────────────────────┘
                          │
                          │ (4) Stdio stream
 ┌────────────────────────┴───────────────────────────────┐
 │  CLAUDE DESKTOP APP                                    │
 └────────────────────────────────────────────────────────┘
```

### Components

| Layer | Technology | Role |
|-------|-----------|------|
| **Data Ingestion** | Python + PySpark | Fetch OHLCV data from public web API, transform |
| **Storage** | Apache Iceberg (Parquet) on local SSD | Durable, queryable historical data |
| **Query Engine** | DuckDB (in FastMCP container) | Read Parquet/Iceberg files directly |
| **MCP Server** | FastMCP (Python, Docker) | Expose DuckDB queries as MCP tools/resources |
| **Client** | Claude Desktop App | Consume MCP tools via stdio |

### Data Flow

1. A Python/PySpark script fetches OHLCV data from **yfinance** (Yahoo Finance) or **investing.com**.
2. Data is processed in-memory on your Mac then persisted to SSD as Apache Iceberg tables backed by Parquet files.
3. A Docker container runs a FastMCP server that bundles DuckDB. The `/warehouse` directory is mounted read-only so DuckDB can query the Parquet/Iceberg files directly without copying.
4. Claude Desktop connects to the FastMCP server via stdio and issues natural-language trading queries.

---

## Implementation Steps

### Step 1: Data Ingestion Script

Create a Python script (`scraper.py`) that:

- Scrapes daily OHLCV for **all S&P 500 constituents** + **VOO**
- Data sources: **yfinance** (Yahoo Finance) and **investing.com**
- Uses PySpark for distributed (or local) processing and transformation
- Handles incremental updates (append new data without duplicating existing records)
- Validates data quality (no gaps, correct types)

**Phase 1**: Daily OHLCV only.  
**Phase 2** (if needed): Hourly OHLCV for intraday analysis.

**Output**: Parquet files organized as an Iceberg table under `~/code/stock_scraper/data/stocks/`.

### Step 2: Warehouse Initialization

- Initialize a local Apache Iceberg catalog pointing to `~/code/stock_scraper/data/`
- Define the Iceberg schema for OHLCV data (e.g., `symbol`, `date`, `open`, `high`, `low`, `close`, `volume`)
- Run the scraper to backfill historical data (e.g., last 5–10 years)

### Step 3: FastMCP + DuckDB Docker Image

Build a Docker image containing:

- Python + FastMCP library
- DuckDB (with Parquet and Iceberg extension)
- A simple MCP server script that exposes:
  - **Tool**: `query_ohlcv(sql_query)` — run arbitrary DuckDB SQL against the warehouse
  - **Resource**: `stocks://{symbol}` — return formatted OHLCV for a ticker
  - **Prompt**: templates for common trading analysis questions

The container mounts `~/warehouse:/warehouse:ro` at runtime.

### Step 4: Claude Desktop Configuration

Edit `claude_desktop_config.json` to register the MCP server:

```json
{
  "mcpServers": {
    "stock-scraper": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-v", "~/warehouse:/warehouse:ro", "stock-scraper-mcp"]
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

- "What is the average daily range for AAPL over the last 6 months?"
- "Find the best range to trade TSLA with an entry within 20% of the current price to maximize completed trades over the last 3 years."

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
export JAVA_HOME=/usr/local/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
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

### 3. Docker Desktop

```bash
brew install --cask docker
```

Docker is used only for the MCP server container. The scraper runs natively on your Mac.

### 4. DuckDB (bundled in the Docker image)

No local install needed — DuckDB lives inside the FastMCP container.

---

## Resource Usage

Approximate disk footprint:

| Component | Size |
|-----------|------|
| OpenJDK 17 (`brew install openjdk@17`) | ~350 MB |
| PySpark (`pip install pyspark`) | ~250 MB |
| Iceberg Spark runtime jar | ~80 MB |
| Docker Desktop (app + Linux VM) | ~2–3 GB |
| **Total** | **~3–3.5 GB** |

### Other concerns

- **Cold start**: PySpark/Java takes ~15–30s to spin up for each scraper run. Since the scraper runs once a day (or on demand), this is negligible.
- **Docker background VM**: Uses ~1–2 GB RAM and some CPU — fine on any modern Mac.
- **Iceberg metadata**: Creates small extra files per table; overhead is < 1 MB.

---

## Development Setup

```bash
# 1. Create and activate a Python virtual environment
python -m venv .venv && source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run data ingestion (backfill)
python scraper.py --backfill --years 5

# 4. Build Docker image for MCP server
docker build -t stock-scraper-mcp -f Dockerfile.mcp .

# 5. Register with Claude Desktop (see Step 4)
```

---

## Requirements

- Python 3.11+
- OpenJDK 17
- PySpark (runs natively on Mac — no cluster needed)
- Apache Iceberg + Parquet (via PySpark)
- Docker Desktop (for FastMCP container)
- DuckDB (bundled in container)

---

## What You'll Learn

- **Distributed data processing concepts** — PySpark DataFrames, partitioning, lazy evaluation
- **Open table formats** — Apache Iceberg catalogs, snapshots, time-travel queries
- **Columnar storage** — Parquet file format, compression, predicate pushdown
- **Local analytics** — DuckDB querying Parquet/Iceberg files directly
- **MCP protocol** — stdio-based server design, tool/resource/prompt exposure
- **Docker packaging** — containerizing a Python app, read-only volume mounts

This project touches the modern data stack — Spark → Iceberg → Parquet → DuckDB — all on a single Mac. Solid portfolio material.

---

## Notes

- All data stays local — no cloud egress costs.
- DuckDB reads Parquet files directly, so queries are fast without a full database setup.
- The Iceberg layer enables schema evolution, time-travel queries, and transactional writes during ingestion.
