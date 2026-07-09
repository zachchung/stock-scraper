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
 ┌───────────────────────────────────────────────────────┐
 │  YOUR MAC STORAGE                                     │
 │                                                        │
 │  /Users/chunluk/warehouse/                             │
 │   └── [ Parquet Data Files & Iceberg Logs ]  ◄──┐      │
 └──────────────────────────────────────────────────│──────┘
                                                     │
 ┌──────────────────────────────────────────────────│──────┐
 │  DOCKER RUNTIME                                  │      │
 │  ┌───────────────────────────────────────────┐   │      │
 │  │ FastMCP Container                         │   │      │
 │  │ - Bundles DuckDB engine                   │───┘      │
 │  │ - Mounts /warehouse folder read-only      │ (3) Scans files
 │  └───────────────────────────────────────────┘          │
 └────────────────────────▲────────────────────────────────┘
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

1. A Python/PySpark script fetches OHLCV data from a public web API (e.g., Yahoo Finance, Alpha Vantage, or Twelve Data).
2. Data is processed in-memory on your Mac then persisted to SSD as Apache Iceberg tables backed by Parquet files.
3. A Docker container runs a FastMCP server that bundles DuckDB. The `/warehouse` directory is mounted read-only so DuckDB can query the Parquet/Iceberg files directly without copying.
4. Claude Desktop connects to the FastMCP server via stdio and issues natural-language trading queries.

---

## Implementation Steps

### Step 1: Data Ingestion Script

Create a Python script (`scraper.py`) that:

- Pulls daily OHLCV data for a configurable list of US stock tickers
- Uses PySpark for distributed (or local) processing and transformation
- Handles incremental updates (append new data without duplicating existing records)
- Validates data quality (no gaps, correct types)

**Output**: Parquet files organized as an Iceberg table under `~//warehouse/stocks/`.

### Step 2: Warehouse Initialization

- Initialize a local Apache Iceberg catalog pointing to `~/warehouse/`
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
- "Which stocks in the S&P 500 have the highest volatility this quarter?"
- "Show me SPY OHLCV for the last 30 trading days."

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
- PySpark (runs natively on Mac — no cluster needed)
- Docker Desktop (for FastMCP container)
- Apache Iceberg + Parquet (via PySpark)
- DuckDB (bundled in container)

---

## Notes

- All data stays local — no cloud egress costs.
- DuckDB reads Parquet files directly, so queries are fast without a full database setup.
- The Iceberg layer enables schema evolution, time-travel queries, and transactional writes during ingestion.
