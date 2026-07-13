#!/usr/bin/env python3
"""FastMCP server that exposes OHLCV data via DuckDB."""

import asyncio
import os
import sys
from pathlib import Path

import duckdb
from fastmcp import FastMCP

DATA_DIR = Path(os.environ.get("STOCK_DATA_DIR", "/data"))
PARQUET_GLOB = str(DATA_DIR / "**/*.parquet")

mcp = FastMCP(
    "Stock OHLCV Server",
    instructions="Query US stock OHLCV data. Tables: ohlcv (symbol, date, open, high, low, close, volume, source)",
)


def get_conn():
    con = duckdb.connect()
    con.execute(f"CREATE VIEW ohlcv AS SELECT * FROM read_parquet('{PARQUET_GLOB}')")
    return con


def fmt(result) -> str:
    return result.fetchdf().to_string(index=False)


@mcp.tool()
def query(sql: str) -> str:
    """Run arbitrary SQL against the OHLCV data."""
    con = get_conn()
    try:
        return fmt(con.sql(sql))
    finally:
        con.close()


@mcp.tool()
def get_stock_data(symbol: str, limit: int = 100) -> str:
    """Get OHLCV data for a ticker symbol. Ordered by date descending."""
    con = get_conn()
    try:
        return fmt(con.sql(
            f"SELECT * FROM ohlcv WHERE symbol = '{symbol.upper()}' ORDER BY date DESC LIMIT {limit}"
        ))
    finally:
        con.close()


@mcp.tool()
def list_symbols() -> str:
    """List all stock symbols available in the data."""
    con = get_conn()
    try:
        return fmt(con.sql("SELECT DISTINCT symbol FROM ohlcv ORDER BY symbol"))
    finally:
        con.close()


@mcp.tool()
def analyze_trades(
    symbol: str,
    entry_range_pct: float = 20.0,
    years: int = 3,
) -> str:
    """Find best price range to trade a stock. Analyzes completed trades
    where entry is within entry_range_pct of the price range low."""
    con = get_conn()
    try:
        return fmt(con.sql(f"""
            WITH price_stats AS (
                SELECT MIN(low) as min_price, MAX(high) as max_price
                FROM ohlcv WHERE symbol = '{symbol.upper()}'
                AND date >= CURRENT_DATE - INTERVAL {years} YEARS
            ),
            ranges AS (
                SELECT
                    min_price + (max_price - min_price) * (i * 0.01) as entry_price,
                    min_price + (max_price - min_price) * ((i + 1) * 0.01) as exit_price
                FROM price_stats, generate_series(0, 99) t(i)
            ),
            trades AS (
                SELECT r.entry_price, r.exit_price,
                    COUNT(*) as trade_count,
                    AVG(
                        CASE WHEN low <= r.entry_price AND high >= r.exit_price
                            THEN (r.exit_price - r.entry_price) / r.entry_price * 100
                            ELSE NULL
                        END
                    ) as avg_return_pct,
                    SUM(
                        CASE WHEN low <= r.entry_price AND high >= r.exit_price
                            THEN 1 ELSE 0
                        END
                    ) as completed_trades
                FROM ranges r
                CROSS JOIN ohlcv o
                WHERE o.symbol = '{symbol.upper()}'
                AND o.date >= CURRENT_DATE - INTERVAL {years} YEARS
                AND r.entry_price >= (SELECT min_price FROM price_stats)
                AND r.entry_price <= (SELECT min_price FROM price_stats) * (1 + {entry_range_pct}/100.0)
                GROUP BY r.entry_price, r.exit_price
            )
            SELECT * FROM trades
            WHERE completed_trades > 0
            ORDER BY completed_trades DESC
            LIMIT 10
        """))
    finally:
        con.close()


if __name__ == "__main__":
    asyncio.run(mcp.run_stdio_async())
