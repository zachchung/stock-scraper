#!/usr/bin/env python3
"""FastMCP server that exposes OHLCV data via DuckDB."""

import asyncio
import os
import sys
from pathlib import Path

import duckdb
from fastmcp import FastMCP

DATA_DIR = Path(os.environ.get("STOCK_DATA_DIR", "/data"))
OHLCV_GLOB = str(DATA_DIR / "stocks/ohlcv_daily/data/*/*.parquet")
INTRADAY_GLOB = str(DATA_DIR / "stocks/ohlcv_intraday/data/*/*.parquet")

mcp = FastMCP(
    "Stock Data Server",
    instructions=(
        "Query US stock data. Available views:\n"
        "- ohlcv: symbol, date, open, high, low, close, volume, source\n"
        "- ohlcv_intraday: symbol, timestamp, open, high, low, close, volume, interval (1h, 30m, etc)\n"
        "- earnings_dates: symbol, report_date (ISO timestamp), eps_estimate, eps_actual, surprise_pct, market_session (pre_market|during_market|post_market)\n"
        "- income_statements: symbol, fiscal_date, total_revenue, gross_profit, operating_income, net_income, diluted_eps"
    ),
)


def get_conn():
    con = duckdb.connect()
    con.execute(f"CREATE VIEW ohlcv AS SELECT * FROM read_parquet('{OHLCV_GLOB}')")

    intraday_path = str(DATA_DIR / "stocks/ohlcv_intraday")
    if (DATA_DIR / "stocks/ohlcv_intraday/metadata").exists():
        try:
            con.execute("LOAD iceberg")
            con.execute(f"CREATE VIEW ohlcv_intraday AS SELECT * FROM iceberg_scan('{intraday_path}')")
        except Exception:
            pass
    elif list((DATA_DIR / "stocks/ohlcv_intraday/data").glob("*/*.parquet")):
        con.execute(f"CREATE VIEW ohlcv_intraday AS SELECT * FROM read_parquet('{INTRADAY_GLOB}')")

    try:
        con.execute("LOAD iceberg")
        earnings_path = str(DATA_DIR / "stocks/earnings_dates")
        if (DATA_DIR / "stocks/earnings_dates/metadata").exists():
            con.execute(f"CREATE VIEW earnings_dates AS SELECT * FROM iceberg_scan('{earnings_path}')")
        income_path = str(DATA_DIR / "stocks/income_statements")
        if (DATA_DIR / "stocks/income_statements/metadata").exists():
            con.execute(f"CREATE VIEW income_statements AS SELECT * FROM iceberg_scan('{income_path}')")
    except Exception:
        pass

    return con


def fmt(result) -> str:
    return result.fetchdf().to_string(index=False)


@mcp.tool()
def query(sql: str) -> str:
    """Run arbitrary SQL against the data warehouse. Available views:
    ohlcv, earnings_dates, income_statements."""
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


@mcp.tool()
def get_earnings(symbol: str, n: int = 10) -> str:
    """Get quarterly earnings dates with EPS surprise for a ticker. Ordered by most recent first."""
    con = get_conn()
    try:
        return fmt(con.sql(f"""
            SELECT symbol,
                   SPLIT_PART(report_date, 'T', 1) as report_date,
                   market_session,
                   eps_estimate,
                   eps_actual,
                   surprise_pct
            FROM earnings_dates
            WHERE symbol = '{symbol.upper()}'
              AND eps_actual IS NOT NULL
            ORDER BY report_date DESC
            LIMIT {n}
        """))
    except Exception as e:
        return f"Error: {e}"
    finally:
        con.close()


@mcp.tool()
def post_earnings_reaction(symbol: str, n: int = 10) -> str:
    """Post-earnings day price reaction for a ticker. Returns report_date,
    market_session, eps_surprise%, first trading day after earnings,
    day 1 open/close/return%, and the overnight gap from previous close.
    Respects pre/post-market release timing."""
    con = get_conn()
    try:
        return fmt(con.sql(f"""
            WITH earnings AS (
                SELECT *,
                       SPLIT_PART(report_date, 'T', 1)::DATE as report_day
                FROM earnings_dates
                WHERE symbol = '{symbol.upper()}'
                  AND eps_actual IS NOT NULL
            ),
            ohlcv_data AS (
                SELECT date, open, close,
                       LAG(close) OVER (ORDER BY date) as prev_close
                FROM ohlcv
                WHERE symbol = '{symbol.upper()}'
            ),
            reactions AS (
                SELECT
                    e.report_date,
                    e.surprise_pct,
                    e.market_session,
                    o.date as reaction_date,
                    o.open,
                    o.close,
                    o.prev_close,
                    ROW_NUMBER() OVER (
                        PARTITION BY e.report_date
                        ORDER BY o.date
                    ) as rn
                FROM earnings e
                JOIN ohlcv_data o ON (
                    (e.market_session = 'post_market' AND o.date > e.report_day)
                    OR (e.market_session != 'post_market' AND o.date >= e.report_day)
                )
            )
            SELECT
                SPLIT_PART(report_date, 'T', 1) as report_date,
                market_session,
                surprise_pct,
                reaction_date,
                open as reaction_open,
                close as reaction_close,
                ROUND((close - open) / open * 100, 2) as intraday_return_pct,
                ROUND((open - prev_close) / prev_close * 100, 2) as overnight_gap_pct
            FROM reactions
            WHERE rn = 1
            ORDER BY report_date DESC
            LIMIT {n}
        """))
    except Exception as e:
        return f"Error: {e}"
    finally:
        con.close()


@mcp.tool()
def get_intraday(symbol: str, interval: str = "1h", days: int = 60) -> str:
    """Get intraday OHLCV bars for a ticker. Ordered by timestamp descending."""
    con = get_conn()
    try:
        return fmt(con.sql(f"""
            SELECT *
            FROM ohlcv_intraday
            WHERE symbol = '{symbol.upper()}'
              AND interval = '{interval}'
              AND timestamp >= CURRENT_DATE - INTERVAL {days} DAYS
            ORDER BY timestamp DESC
        """))
    except Exception as e:
        return f"Error: {e}"
    finally:
        con.close()


@mcp.tool()
def intraday_pattern(
    symbol: str,
    condition: str = "gap_up",
    interval: str = "1h",
    gap_threshold_pct: float = 2.0,
) -> str:
    """Analyze intraday price patterns. Currently supports 'gap_up' condition:
    finds the hour-bar distribution of when the intraday high occurs on gap-up
    days (open > previous_close by gap_threshold_pct percent)."""
    con = get_conn()
    try:
        return fmt(con.sql(f"""
            WITH daily AS (
                SELECT date, open, close,
                       LAG(close) OVER (ORDER BY date) as prev_close
                FROM ohlcv
                WHERE symbol = '{symbol.upper()}'
            ),
            gap_days AS (
                SELECT date, open, prev_close,
                       ROUND((open / prev_close - 1) * 100, 2) as gap_pct
                FROM daily
                WHERE prev_close IS NOT NULL
                  AND open > prev_close * (1 + {gap_threshold_pct}/100.0)
            ),
            hourly_rank AS (
                SELECT
                    i.timestamp::DATE as trade_date,
                    i.high as bar_high,
                    i.timestamp,
                    EXTRACT(HOUR FROM i.timestamp) as bar_hour,
                    ROW_NUMBER() OVER (
                        PARTITION BY i.symbol, i.timestamp::DATE
                        ORDER BY i.high DESC
                    ) as rn
                FROM ohlcv_intraday i
                WHERE i.symbol = '{symbol.upper()}'
                  AND i.interval = '{interval}'
                  AND i.timestamp::DATE IN (SELECT date FROM gap_days)
            )
            SELECT
                bar_hour,
                COUNT(*) as days_count,
                ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 1) as pct
            FROM hourly_rank
            WHERE rn = 1
            GROUP BY bar_hour
            ORDER BY bar_hour
        """))
    except Exception as e:
        return f"Error: {e}"
    finally:
        con.close()


if __name__ == "__main__":
    asyncio.run(mcp.run_stdio_async())
