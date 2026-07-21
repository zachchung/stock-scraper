#!/usr/bin/env python3
"""Stock scraper: fetch OHLCV data via yfinance and store as Iceberg tables."""

import argparse
import sys
from datetime import datetime, timedelta

import duckdb
import pandas as pd
import requests
import yfinance as yf
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, to_date

WAREHOUSE_PATH = "/Users/ZacharyChung1/code/stock_scraper/data"
ICEBERG_VERSION = "org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0"
TABLE_NAME = "local.stocks.ohlcv"

SPARK = None

def get_spark():
    global SPARK
    if SPARK is None:
        SPARK = (
            SparkSession.builder
            .appName("stock_scraper")
            .master("local[*]")
            .config("spark.sql.catalog.local", "org.apache.iceberg.spark.SparkCatalog")
            .config("spark.sql.catalog.local.type", "hadoop")
            .config("spark.sql.catalog.local.warehouse", WAREHOUSE_PATH)
            .config("spark.jars.packages", ICEBERG_VERSION)
            .getOrCreate()
        )
    return SPARK

def get_sp500_tickers():
    resp = requests.get(
        "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv",
        timeout=15,
    )
    resp.raise_for_status()
    df = pd.read_csv(pd.io.common.StringIO(resp.text))
    return sorted(df["Symbol"].tolist())

def fetch_ohlcv(ticker, years=5, start_date=None):
    if start_date:
        start = (start_date + timedelta(days=1)).strftime("%Y-%m-%d")
        end = datetime.today().strftime("%Y-%m-%d")
        hist = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
    else:
        stock = yf.Ticker(ticker)
        hist = stock.history(period=f"{years}y")
    if hist.empty:
        return None
    df = hist.reset_index()[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()
    df.columns = ["date", "open", "high", "low", "close", "volume"]
    df["symbol"] = ticker
    df["source"] = "yfinance"
    return df

def write_to_iceberg(df):
    spark = get_spark()
    sdf = (
        spark.createDataFrame(df)
        .withColumn("date", to_date(col("date")))
        .dropDuplicates(["symbol", "date"])
    )
    sdf.createOrReplaceTempView("batch")

    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            symbol STRING,
            date DATE,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume LONG,
            source STRING
        )
        USING iceberg
        PARTITIONED BY (symbol)
    """)

    spark.sql(f"""
        MERGE INTO {TABLE_NAME} t
        USING batch b
        ON t.symbol = b.symbol AND t.date = b.date
        WHEN NOT MATCHED THEN INSERT *
    """)

def get_latest_dates():
    parquet_path = f"{WAREHOUSE_PATH}/stocks/ohlcv/data/*/*.parquet"
    con = duckdb.connect()
    try:
        df = con.execute(f"""
            SELECT symbol, MAX(date) as last_date
            FROM read_parquet('{parquet_path}')
            GROUP BY symbol
        """).fetchdf()
        return df.set_index('symbol')['last_date'].to_dict()
    except Exception:
        return {}
    finally:
        con.close()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", action="store_true", help="Backfill historical data")
    parser.add_argument("--years", type=int, default=5, help="Years of history to fetch")
    parser.add_argument("--tickers", nargs="+", help="Specific tickers to scrape (default: S&P 500 + VOO)")
    parser.add_argument("--incremental", action="store_true", help="Fetch only recent days")
    args = parser.parse_args()

    if args.tickers:
        tickers = args.tickers
    else:
        tickers = get_sp500_tickers()
        tickers.append("VOO")

    if args.incremental and not args.backfill:
        latest_dates = get_latest_dates()
    else:
        latest_dates = {}

    start = datetime.now()
    total = len(tickers)
    for i, ticker in enumerate(tickers, 1):
        try:
            last_date = latest_dates.get(ticker) if latest_dates else None
            if last_date is not None:
                df = fetch_ohlcv(ticker, start_date=last_date)
            else:
                df = fetch_ohlcv(ticker, years=args.years)
            if df is not None and not df.empty:
                write_to_iceberg(df)
            label = "incr" if last_date is not None else "full"
            print(f"[{i}/{total}] {ticker} ({label}) done", flush=True)
        except Exception as e:
            print(f"[{i}/{total}] {ticker} FAILED: {e}", file=sys.stderr, flush=True)

    elapsed = datetime.now() - start
    print(f"\nCompleted in {elapsed}", flush=True)

if __name__ == "__main__":
    main()
