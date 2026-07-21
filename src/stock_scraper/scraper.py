#!/usr/bin/env python3
"""Stock scraper: fetch OHLCV, earnings dates, and income statements via yfinance."""

import argparse
import sys
from datetime import datetime, time, timedelta

import duckdb
import pandas as pd
import requests
import yfinance as yf
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, to_date

WAREHOUSE_PATH = "/Users/ZacharyChung1/code/stock_scraper/data"
ICEBERG_VERSION = "org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0"
OHLCV_TABLE = "local.stocks.ohlcv"
EARNINGS_TABLE = "local.stocks.earnings_dates"
INCOME_TABLE = "local.stocks.income_statements"

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

def market_session(dt: datetime) -> str:
    t = dt.time()
    if t < time(9, 30):
        return "pre_market"
    elif t < time(16, 0):
        return "during_market"
    return "post_market"

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

def fetch_earnings(ticker):
    stock = yf.Ticker(ticker)
    ed = stock.earnings_dates
    if ed is None or ed.empty:
        return None
    df = ed.copy()
    df = df.reset_index()
    df.columns = ["report_date", "eps_estimate", "eps_actual", "surprise_pct"]
    df["market_session"] = df["report_date"].apply(
        lambda x: market_session(x.to_pydatetime())
    )
    df["report_date"] = df["report_date"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    df["symbol"] = ticker
    return df[["symbol", "report_date", "eps_estimate", "eps_actual",
               "surprise_pct", "market_session"]]

INCOME_METRICS = [
    ("total_revenue", "Total Revenue"),
    ("gross_profit", "Gross Profit"),
    ("operating_income", "Operating Income"),
    ("net_income", "Net Income"),
    ("diluted_eps", "Diluted EPS"),
    ("ebit", "EBIT"),
    ("ebitda", "EBITDA"),
]

def fetch_income_statements(ticker):
    stock = yf.Ticker(ticker)
    qis = stock.quarterly_income_stmt
    if qis is None or qis.empty:
        return None
    rows = []
    for col_name, source_name in INCOME_METRICS:
        if source_name in qis.index:
            for fiscal_date in qis.columns:
                val = qis.loc[source_name, fiscal_date]
                if pd.notna(val):
                    rows.append({
                        "symbol": ticker,
                        "fiscal_date": fiscal_date.to_pydatetime().date(),
                        "metric": col_name,
                        "value": float(val),
                    })
    if not rows:
        return None
    df = pd.DataFrame(rows)
    pivoted = df.pivot_table(
        index=["symbol", "fiscal_date"],
        columns="metric",
        values="value",
    ).reset_index()
    pivoted.columns.name = None
    for col_name, _ in INCOME_METRICS:
        if col_name not in pivoted.columns:
            pivoted[col_name] = float('nan')
    return pivoted

def write_to_iceberg(df):
    spark = get_spark()
    sdf = (
        spark.createDataFrame(df)
        .withColumn("date", to_date(col("date")))
        .dropDuplicates(["symbol", "date"])
    )
    sdf.createOrReplaceTempView("batch")

    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {OHLCV_TABLE} (
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
        MERGE INTO {OHLCV_TABLE} t
        USING batch b
        ON t.symbol = b.symbol AND t.date = b.date
        WHEN NOT MATCHED THEN INSERT *
    """)

def write_earnings_to_iceberg(df):
    spark = get_spark()
    sdf = (
        spark.createDataFrame(df)
        .dropDuplicates(["symbol", "report_date"])
    )
    sdf.createOrReplaceTempView("batch")

    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {EARNINGS_TABLE} (
            symbol STRING,
            report_date STRING,
            eps_estimate DOUBLE,
            eps_actual DOUBLE,
            surprise_pct DOUBLE,
            market_session STRING
        )
        USING iceberg
        PARTITIONED BY (symbol)
    """)

    spark.sql(f"""
        MERGE INTO {EARNINGS_TABLE} t
        USING batch b
        ON t.symbol = b.symbol AND t.report_date = b.report_date
        WHEN NOT MATCHED THEN INSERT *
    """)

def write_income_to_iceberg(df):
    spark = get_spark()
    sdf = (
        spark.createDataFrame(df)
        .dropDuplicates(["symbol", "fiscal_date"])
    )
    sdf.createOrReplaceTempView("batch")

    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {INCOME_TABLE} (
            symbol STRING,
            fiscal_date DATE,
            total_revenue DOUBLE,
            gross_profit DOUBLE,
            operating_income DOUBLE,
            net_income DOUBLE,
            diluted_eps DOUBLE,
            ebit DOUBLE,
            ebitda DOUBLE
        )
        USING iceberg
        PARTITIONED BY (symbol)
    """)

    spark.sql(f"""
        MERGE INTO {INCOME_TABLE} t
        USING batch b
        ON t.symbol = b.symbol AND t.fiscal_date = b.fiscal_date
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
    parser.add_argument("--backfill", action="store_true", help="Backfill historical OHLCV data")
    parser.add_argument("--years", type=int, default=5, help="Years of history to fetch")
    parser.add_argument("--tickers", nargs="+", help="Specific tickers to scrape (default: S&P 500 + VOO)")
    parser.add_argument("--incremental", action="store_true", help="Fetch only recent OHLCV days")
    parser.add_argument("--earnings", action="store_true", help="Fetch earnings dates and income statements")
    args = parser.parse_args()

    if args.tickers:
        tickers = args.tickers
    else:
        tickers = get_sp500_tickers()
        tickers.append("VOO")

    do_ohlcv = args.backfill or args.incremental
    do_earnings = args.earnings

    if not do_ohlcv and not do_earnings:
        parser.print_help()
        sys.exit(1)

    start = datetime.now()

    if do_ohlcv:
        if args.incremental and not args.backfill:
            latest_dates = get_latest_dates()
        else:
            latest_dates = {}

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
                print(f"[{i}/{total}] OHLCV {ticker} ({label}) done", flush=True)
            except Exception as e:
                print(f"[{i}/{total}] OHLCV {ticker} FAILED: {e}", file=sys.stderr, flush=True)

    if do_earnings:
        total = len(tickers)
        for i, ticker in enumerate(tickers, 1):
            try:
                edf = fetch_earnings(ticker)
                if edf is not None and not edf.empty:
                    write_earnings_to_iceberg(edf)
                idf = fetch_income_statements(ticker)
                if idf is not None and not idf.empty:
                    write_income_to_iceberg(idf)
                label = ""
                if edf is not None:
                    label += f" earnings({len(edf)})"
                if idf is not None:
                    label += f" income({len(idf)})"
                print(f"[{i}/{total}] EARN {ticker} ({label.strip()}) done", flush=True)
            except Exception as e:
                print(f"[{i}/{total}] EARN {ticker} FAILED: {e}", file=sys.stderr, flush=True)

    elapsed = datetime.now() - start
    print(f"\nCompleted in {elapsed}", flush=True)

if __name__ == "__main__":
    main()
