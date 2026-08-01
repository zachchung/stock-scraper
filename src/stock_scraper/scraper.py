#!/usr/bin/env python3
"""Stock scraper: fetch OHLCV, earnings dates, and income statements via yfinance."""

import argparse
import sys
from datetime import date, datetime, time, timedelta

import duckdb
import pandas as pd
import requests
import yfinance as yf
from dateutil.relativedelta import relativedelta
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, to_date

WAREHOUSE_PATH = "/Users/ZacharyChung1/code/stock_scraper/data"
ICEBERG_VERSION = "org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0"
OHLCV_TABLE_DAILY = "local.stocks.ohlcv_daily"
OHLCV_TABLE_INTRADAY = "local.stocks.ohlcv_intraday"
EARNINGS_TABLE = "local.stocks.earnings_dates"
INCOME_TABLE = "local.stocks.income_statements"
ANALYST_TARGETS_TABLE = "local.stocks.analyst_targets"
ANALYST_UPGRADES_TABLE = "local.stocks.analyst_upgrades_downgrades"
EPS_ESTIMATES_TABLE = "local.stocks.eps_estimates"
FUNDAMENTALS_TABLE = "local.stocks.fundamentals_snapshot"

INTRADAY_INTERVALS = {"1m": "7d", "2m": "60d", "5m": "60d", "15m": "60d", "30m": "60d", "60m": "730d", "1h": "730d"}
DEFAULT_INTRADAY_INTERVAL = "1h"
VALID_PERIODS = ["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"]

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

def fetch_ohlcv_daily(ticker, period="max", start_date=None):
    # start_date is for incremental load
    if start_date:
        start = (start_date + timedelta(days=1)).strftime("%Y-%m-%d")
        end = datetime.today().strftime("%Y-%m-%d")
        hist = yf.download(ticker, start=start, end=end, auto_adjust=False)
    else:
        hist = yf.download(ticker, period=period, auto_adjust=False)
    if hist.empty:
        return None
    df = hist.reset_index()[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()
    df.columns = ["date", "open", "high", "low", "close", "volume"]
    df["symbol"] = ticker
    df["source"] = "yfinance"
    return df

def fetch_ohlcv_intraday(ticker, interval="1h", period="max", start_date=None, prepost=False):
    # start_date is for incremental load
    if start_date:
        hist = yf.download(ticker, start=start_date.strftime("%Y-%m-%d"), interval=interval, prepost=prepost, auto_adjust=False)
    else:
        hist = yf.download(ticker, period=period, interval=interval, prepost=prepost, auto_adjust=False)
    if hist.empty:
        return None
    df = hist.reset_index()[["Datetime", "Open", "High", "Low", "Close", "Volume"]].copy()
    df.columns = ["timestamp", "open", "high", "low", "close", "volume"]
    if df["timestamp"].dt.tz is not None:
        df["timestamp"] = df["timestamp"].dt.tz_convert("America/New_York")
    df["symbol"] = ticker
    df["interval"] = interval
    return df

def get_fiscal_year_end(ticker):
    """Return (last_fiscal_year_end, next_fiscal_year_end) dates for a ticker."""
    stock = yf.Ticker(ticker)
    info = stock.info
    last = info.get("lastFiscalYearEnd")
    nxt = info.get("nextFiscalYearEnd")
    if not last or not nxt:
        return None, None
    return (
        datetime.fromtimestamp(last).date(),
        datetime.fromtimestamp(nxt).date(),
    )

def generate_fiscal_quarter_ends(fiscal_year_end, n=40):
    """Generate fiscal quarter-end dates by stepping back 3 months from a
    fiscal year-end. Returned list is descending (most recent first)."""
    ends = []
    d = fiscal_year_end
    for _ in range(n):
        ends.append(d)
        d = d - relativedelta(months=3)
    return ends

def fiscal_labels_for_reports(report_dates, last_fye, next_fye):
    """Map each report date to its fiscal period. Returns a DataFrame with
    columns fiscal_period_end (date), fiscal_quarter (int), fiscal_year (int).

    Each earnings report covers the fiscal quarter that ended most recently
    before the report date. Quarter numbering uses the fiscal year-end as Q4,
    stepping back 3 months per quarter.
    """
    if last_fye is None or next_fye is None:
        return pd.DataFrame(index=report_dates.index)
    qends = generate_fiscal_quarter_ends(next_fye)
    rows = []
    for rd in report_dates:
        rd = rd.to_pydatetime().date() if hasattr(rd, "to_pydatetime") else rd
        # fiscal period end = latest generated quarter end <= report date
        period_end = next((q for q in qends if q <= rd), None)
        if period_end is None:
            rows.append((None, None, None))
            continue
        # fiscal year: the fiscal year that this quarter belongs to.
        # next_fye is the FYE of the fiscal year ending after the report.
        delta_months = (next_fye.year * 12 + next_fye.month) - (
            period_end.year * 12 + period_end.month
        )
        # delta=0 -> Q4 (FYE quarter), 3 -> Q3, 6 -> Q2, 9 -> Q1
        qnum = 4 - (delta_months % 12) // 3
        fy = next_fye.year - delta_months // 12
        rows.append((period_end, qnum, fy))
    df = pd.DataFrame(rows, columns=["fiscal_period_end", "fiscal_quarter", "fiscal_year"])
    df.index = list(report_dates)
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

    last_fye, next_fye = get_fiscal_year_end(ticker)
    labels = fiscal_labels_for_reports(ed.index, last_fye, next_fye)
    df = df.join(labels.reset_index(drop=True))
    return df[["symbol", "report_date", "eps_estimate", "eps_actual",
               "surprise_pct", "market_session", "fiscal_period_end",
               "fiscal_quarter", "fiscal_year"]]

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
    pivoted["net_profit_margin"] = pivoted["net_income"] / pivoted["total_revenue"]
    return pivoted

def fetch_analyst_targets(ticker):
    stock = yf.Ticker(ticker)
    info = stock.info
    targets = {}
    targets["symbol"] = ticker
    targets["current_price"] = info.get("currentPrice")
    targets["target_high"] = info.get("targetHighPrice")
    targets["target_low"] = info.get("targetLowPrice")
    targets["target_mean"] = info.get("targetMeanPrice")
    targets["target_median"] = info.get("targetMedianPrice")
    targets["recommendation_mean"] = info.get("recommendationMean")
    targets["recommendation_key"] = info.get("recommendationKey")
    targets["num_analysts"] = info.get("numberOfAnalystOpinions")
    targets["fetched_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z")
    return targets

def fetch_upgrades_downgrades(ticker):
    stock = yf.Ticker(ticker)
    ud = stock.upgrades_downgrades
    if ud is None or ud.empty:
        return None
    df = ud.reset_index().copy()
    df.columns = ["grade_date", "firm", "to_grade", "from_grade", "action",
                   "price_target_action", "price_target", "prior_price_target"]
    df["grade_date"] = df["grade_date"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    df["symbol"] = ticker
    return df[["symbol", "grade_date", "firm", "to_grade", "from_grade",
               "action", "price_target", "prior_price_target"]]

EPS_ESTIMATE_PERIODS = {"0q": "current_quarter", "+1q": "next_quarter",
                        "0y": "current_year", "+1y": "next_year"}

def fetch_eps_estimates(ticker):
    """Long-format consensus EPS estimates: 0q/+1q (quarters) and 0y/+1y (fiscal years)."""
    stock = yf.Ticker(ticker)
    ee = stock.earnings_estimate
    if ee is None or ee.empty:
        return None
    fetched_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z")
    rows = []
    for period in ee.index:
        if period not in EPS_ESTIMATE_PERIODS:
            continue
        r = ee.loc[period]
        rows.append({
            "symbol": ticker,
            "fetched_at": fetched_at,
            "period": period,
            "period_label": EPS_ESTIMATE_PERIODS[period],
            "eps_avg": r.get("avg"),
            "eps_low": r.get("low"),
            "eps_high": r.get("high"),
            "num_analysts": r.get("numberOfAnalysts"),
        })
    if not rows:
        return None
    return pd.DataFrame(rows)

def fetch_fundamentals_snapshot(ticker):
    """Point-in-time valuation snapshot from Ticker.info."""
    stock = yf.Ticker(ticker)
    info = stock.info
    return {
        "symbol": ticker,
        "fetched_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z"),
        "market_cap": info.get("marketCap"),
        "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
        "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
        "all_time_high": info.get("allTimeHigh"),
        "all_time_low": info.get("allTimeLow"),
        "profit_margin": info.get("profitMargins"),
        "shares_outstanding": info.get("sharesOutstanding"),
        "eps_ttm": info.get("epsTrailingTwelveMonths"),
        "eps_current_year": info.get("epsCurrentYear"),
        "forward_eps": info.get("forwardEps"),
        "trailing_pe": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "price_to_book": info.get("priceToBook"),
        "book_value": info.get("bookValue"),
        "current_ratio": info.get("currentRatio"),
        "quick_ratio": info.get("quickRatio"),
        "debt_to_equity": info.get("debtToEquity"),
        "return_on_equity": info.get("returnOnEquity"),
        "free_cashflow": info.get("freeCashflow"),
        "last_fiscal_year_end": (
            datetime.fromtimestamp(info["lastFiscalYearEnd"]).date()
            if info.get("lastFiscalYearEnd") else None
        ),
        "next_fiscal_year_end": (
            datetime.fromtimestamp(info["nextFiscalYearEnd"]).date()
            if info.get("nextFiscalYearEnd") else None
        ),
    }

def write_ohlcv_daily_to_iceberg(df):
    spark = get_spark()
    sdf = (
        spark.createDataFrame(df)
        .withColumn("date", to_date(col("date")))
        .dropDuplicates(["symbol", "date"])
    )
    sdf.createOrReplaceTempView("batch")

    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {OHLCV_TABLE_DAILY} (
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
        MERGE INTO {OHLCV_TABLE_DAILY} t
        USING batch b
        ON t.symbol = b.symbol AND t.date = b.date
        WHEN NOT MATCHED THEN INSERT *
    """)

def write_ohlcv_intraday_to_iceberg(df):
    spark = get_spark()
    sdf = spark.createDataFrame(df).dropDuplicates(["symbol", "timestamp", "interval"])
    sdf.createOrReplaceTempView("batch")
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {OHLCV_TABLE_INTRADAY} (
            symbol STRING,
            timestamp TIMESTAMP,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume LONG,
            interval STRING
        )
        USING iceberg
        PARTITIONED BY (symbol)
    """)
    spark.sql(f"""
        MERGE INTO {OHLCV_TABLE_INTRADAY} t
        USING batch b
        ON t.symbol = b.symbol AND t.timestamp = b.timestamp AND t.interval = b.interval
        WHEN NOT MATCHED THEN INSERT *
    """)

def write_earnings_to_iceberg(df):
    spark = get_spark()
    sdf = (
        spark.createDataFrame(df)
        .withColumn("fiscal_period_end", to_date(col("fiscal_period_end")))
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
            market_session STRING,
            fiscal_period_end DATE,
            fiscal_quarter INT,
            fiscal_year INT
        )
        USING iceberg
        PARTITIONED BY (symbol)
    """)

    # Add columns to pre-existing tables created before the fiscal labels.
    existing_cols = {c.name for c in spark.table(EARNINGS_TABLE).schema}
    for col_name, col_type in [
        ("fiscal_period_end", "DATE"),
        ("fiscal_quarter", "INT"),
        ("fiscal_year", "INT"),
    ]:
        if col_name not in existing_cols:
            spark.sql(f"ALTER TABLE {EARNINGS_TABLE} ADD COLUMN {col_name} {col_type}")

    spark.sql(f"""
        MERGE INTO {EARNINGS_TABLE} AS target
        USING batch AS source
        ON target.symbol = source.symbol AND target.report_date = source.report_date
        WHEN MATCHED THEN UPDATE SET *
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
            ebitda DOUBLE,
            net_profit_margin DOUBLE
        )
        USING iceberg
        PARTITIONED BY (symbol)
    """)

    existing_cols = {c.name for c in spark.table(INCOME_TABLE).schema}
    if "net_profit_margin" not in existing_cols:
        spark.sql(f"ALTER TABLE {INCOME_TABLE} ADD COLUMN net_profit_margin DOUBLE")

    spark.sql(f"""
        MERGE INTO {INCOME_TABLE} t
        USING batch b
        ON t.symbol = b.symbol AND t.fiscal_date = b.fiscal_date
        WHEN MATCHED THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *
    """)

def write_analyst_targets_to_iceberg(targets_dict):
    spark = get_spark()
    df = pd.DataFrame([targets_dict])
    sdf = spark.createDataFrame(df)
    sdf.createOrReplaceTempView("batch")

    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {ANALYST_TARGETS_TABLE} (
            symbol STRING,
            current_price DOUBLE,
            target_high DOUBLE,
            target_low DOUBLE,
            target_mean DOUBLE,
            target_median DOUBLE,
            recommendation_mean DOUBLE,
            recommendation_key STRING,
            num_analysts INT,
            fetched_at STRING
        )
        USING iceberg
        PARTITIONED BY (symbol)
    """)

    spark.sql(f"""
        INSERT INTO {ANALYST_TARGETS_TABLE}
        SELECT * FROM batch
    """)

def write_upgrades_downgrades_to_iceberg(df):
    spark = get_spark()
    sdf = spark.createDataFrame(df).dropDuplicates(["symbol", "grade_date", "firm"])
    sdf.createOrReplaceTempView("batch")
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {ANALYST_UPGRADES_TABLE} (
            symbol STRING,
            grade_date STRING,
            firm STRING,
            to_grade STRING,
            from_grade STRING,
            action STRING,
            price_target DOUBLE,
            prior_price_target DOUBLE
        )
        USING iceberg
        PARTITIONED BY (symbol)
    """)
    spark.sql(f"""
        MERGE INTO {ANALYST_UPGRADES_TABLE} t
        USING batch b
        ON t.symbol = b.symbol AND t.grade_date = b.grade_date AND t.firm = b.firm
        WHEN NOT MATCHED THEN INSERT *
    """)

def write_eps_estimates_to_iceberg(df):
    spark = get_spark()
    sdf = spark.createDataFrame(df).dropDuplicates(
        ["symbol", "fetched_at", "period"])
    sdf.createOrReplaceTempView("batch")
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {EPS_ESTIMATES_TABLE} (
            symbol STRING,
            fetched_at STRING,
            period STRING,
            period_label STRING,
            eps_avg DOUBLE,
            eps_low DOUBLE,
            eps_high DOUBLE,
            num_analysts INT
        )
        USING iceberg
        PARTITIONED BY (symbol)
    """)
    spark.sql(f"""
        INSERT INTO {EPS_ESTIMATES_TABLE}
        SELECT * FROM batch
    """)

def write_fundamentals_to_iceberg(snapshot_dict):
    spark = get_spark()
    df = pd.DataFrame([snapshot_dict])
    sdf = spark.createDataFrame(df)
    sdf.createOrReplaceTempView("batch")
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {FUNDAMENTALS_TABLE} (
            symbol STRING,
            fetched_at STRING,
            market_cap DOUBLE,
            fifty_two_week_high DOUBLE,
            fifty_two_week_low DOUBLE,
            all_time_high DOUBLE,
            all_time_low DOUBLE,
            profit_margin DOUBLE,
            shares_outstanding DOUBLE,
            eps_ttm DOUBLE,
            eps_current_year DOUBLE,
            forward_eps DOUBLE,
            trailing_pe DOUBLE,
            forward_pe DOUBLE,
            price_to_book DOUBLE,
            book_value DOUBLE,
            current_ratio DOUBLE,
            quick_ratio DOUBLE,
            debt_to_equity DOUBLE,
            return_on_equity DOUBLE,
            free_cashflow DOUBLE,
            last_fiscal_year_end DATE,
            next_fiscal_year_end DATE
        )
        USING iceberg
        PARTITIONED BY (symbol)
    """)

    existing_cols = {c.name for c in spark.table(FUNDAMENTALS_TABLE).schema}
    for col_name in [
        "all_time_high", "all_time_low", "trailing_pe", "forward_pe",
        "price_to_book", "book_value", "current_ratio", "quick_ratio",
        "debt_to_equity", "return_on_equity", "free_cashflow",
    ]:
        if col_name not in existing_cols:
            spark.sql(f"ALTER TABLE {FUNDAMENTALS_TABLE} ADD COLUMN {col_name} DOUBLE")

    cols = [
        "symbol", "fetched_at", "market_cap", "fifty_two_week_high", "fifty_two_week_low",
        "all_time_high", "all_time_low", "profit_margin", "shares_outstanding",
        "eps_ttm", "eps_current_year", "forward_eps", "trailing_pe", "forward_pe",
        "price_to_book", "book_value", "current_ratio", "quick_ratio",
        "debt_to_equity", "return_on_equity", "free_cashflow",
        "last_fiscal_year_end", "next_fiscal_year_end",
    ]
    col_list = ", ".join(cols)
    spark.sql(f"""
        INSERT INTO {FUNDAMENTALS_TABLE} ({col_list})
        SELECT {col_list} FROM batch
    """)

def get_latest_dates():
    parquet_path = f"{WAREHOUSE_PATH}/stocks/ohlcv_daily/data/*/*.parquet"
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

def get_latest_intraday_timestamps(interval=DEFAULT_INTRADAY_INTERVAL):
    parquet_path = f"{WAREHOUSE_PATH}/stocks/ohlcv_intraday/data/**/*.parquet"
    con = duckdb.connect()
    try:
        df = con.execute(f"""
            SELECT symbol, MAX(timestamp) as last_timestamp
            FROM read_parquet('{parquet_path}', union_by_name=true)
            WHERE interval = '{interval}'
            GROUP BY symbol
        """).fetchdf()
        return df.set_index('symbol')['last_timestamp'].to_dict()
    except Exception:
        return {}
    finally:
        con.close()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily", action="store_true", help="Backfill historical OHLCV data")
    parser.add_argument("--period", type=str, default="max", choices=VALID_PERIODS,
                        help="yfinance period: 1d,5d,1mo,3mo,6mo,1y,2y,5y,10y,ytd,max  (default: max)")
    parser.add_argument("--incremental", action="store_true", help="Fetch only recent rows (daily or intraday)")
    parser.add_argument("--intraday", action="store_true", help="Fetch intraday OHLCV data")
    parser.add_argument("--interval", type=str, default=DEFAULT_INTRADAY_INTERVAL,
                        choices=list(INTRADAY_INTERVALS.keys()),
                        help="Intraday bar interval")
    parser.add_argument("--prepost", action="store_true", default=False,
                        help="Include pre/post market data in intraday fetch (unreliable — yfinance artifact spikes)")
    parser.add_argument("--earnings", action="store_true", help="Fetch earnings dates and income statements")
    parser.add_argument("--fundamentals", action="store_true", help="Fetch fundamentals snapshot (market cap, 52w high/low, profit margin)")
    parser.add_argument("--targets", action="store_true", help="Fetch analyst consensus price targets only (snapshot, appended on each run)")
    parser.add_argument("--analyst", action="store_true", help="Fetch analyst price targets AND upgrades/downgrades (both)")
    parser.add_argument("--tickers", nargs="+", help="Specific tickers to scrape (default: S&P 500 + VOO)")
    args = parser.parse_args()

    if args.tickers:
        tickers = args.tickers
    else:
        tickers = get_sp500_tickers()
        tickers.append("VOO")

    do_ohlcv_daily = args.daily
    do_ohlcv_intraday = args.intraday
    do_earnings = args.earnings
    do_fundamentals = args.fundamentals
    do_targets = args.targets
    do_analyst = args.analyst

    if not do_ohlcv_daily and not do_ohlcv_intraday and not do_earnings and not do_fundamentals and not do_targets and not do_analyst:
        parser.print_help()
        sys.exit(1)

    start = datetime.now()

    if do_ohlcv_daily:
        if args.incremental:
            latest_dates = get_latest_dates()
        else:
            latest_dates = {}

        total = len(tickers)
        for i, ticker in enumerate(tickers, 1):
            try:
                last_date = latest_dates.get(ticker) if latest_dates else None
                if last_date is not None:
                    df = fetch_ohlcv_daily(ticker, start_date=last_date)
                else:
                    df = fetch_ohlcv_daily(ticker, period=args.period)
                if df is not None and not df.empty:
                    write_ohlcv_daily_to_iceberg(df)
                label = "incr" if last_date is not None else "full"
                print(f"[{i}/{total}] OHLCV {ticker} ({label}) done", flush=True)
            except Exception as e:
                print(f"[{i}/{total}] OHLCV {ticker} FAILED: {e}", file=sys.stderr, flush=True)

    if do_ohlcv_intraday:
        if args.incremental:
            latest_intraday_ts = get_latest_intraday_timestamps(args.interval)
        else:
            latest_intraday_ts = {}

        total = len(tickers)
        for i, ticker in enumerate(tickers, 1):
            try:
                last_ts = latest_intraday_ts.get(ticker) if latest_intraday_ts else None
                if last_ts is not None:
                    df = fetch_ohlcv_intraday(ticker, interval=args.interval, start_date=last_ts, prepost=args.prepost)
                else:
                    df = fetch_ohlcv_intraday(ticker, interval=args.interval, period=args.period, prepost=args.prepost)
                if df is not None and not df.empty:
                    write_ohlcv_intraday_to_iceberg(df)
                label = "incr" if last_ts is not None else "full"
                print(f"[{i}/{total}] INTRADAY {ticker} ({args.interval}) {label} done ({len(df) if df is not None else 0} bars)", flush=True)
            except Exception as e:
                print(f"[{i}/{total}] INTRADAY {ticker} FAILED: {e}", file=sys.stderr, flush=True)

    if do_earnings:
        total = len(tickers)
        for i, ticker in enumerate(tickers, 1):
            try:
                parts = []
                edf = fetch_earnings(ticker)
                if edf is not None and not edf.empty:
                    write_earnings_to_iceberg(edf)
                    parts.append(f"earnings({len(edf)})")
                idf = fetch_income_statements(ticker)
                if idf is not None and not idf.empty:
                    write_income_to_iceberg(idf)
                    parts.append(f"income({len(idf)})")
                eef = fetch_eps_estimates(ticker)
                if eef is not None and not eef.empty:
                    write_eps_estimates_to_iceberg(eef)
                    parts.append(f"eps_estimates({len(eef)})")
                print(f"[{i}/{total}] EARN {ticker} ({' '.join(parts)}) done", flush=True)
            except Exception as e:
                print(f"[{i}/{total}] EARN {ticker} FAILED: {e}", file=sys.stderr, flush=True)

    if do_fundamentals:
        total = len(tickers)
        for i, ticker in enumerate(tickers, 1):
            try:
                fund = fetch_fundamentals_snapshot(ticker)
                if fund and fund.get("market_cap") is not None:
                    write_fundamentals_to_iceberg(fund)
                    print(f"[{i}/{total}] FUND {ticker} (fundamentals) done", flush=True)
                else:
                    print(f"[{i}/{total}] FUND {ticker} done", flush=True)
            except Exception as e:
                print(f"[{i}/{total}] FUND {ticker} FAILED: {e}", file=sys.stderr, flush=True)

    if do_targets or do_analyst:
        total = len(tickers)
        for i, ticker in enumerate(tickers, 1):
            try:
                parts = []
                if do_targets or do_analyst:
                    targets = fetch_analyst_targets(ticker)
                    if targets:
                        write_analyst_targets_to_iceberg(targets)
                        parts.append(f"targets")
                if do_analyst:
                    udf = fetch_upgrades_downgrades(ticker)
                    if udf is not None and not udf.empty:
                        write_upgrades_downgrades_to_iceberg(udf)
                        parts.append(f"upgrades({len(udf)})")
                print(f"[{i}/{total}] ANALYST {ticker} ({' '.join(parts)}) done", flush=True)
            except Exception as e:
                print(f"[{i}/{total}] ANALYST {ticker} FAILED: {e}", file=sys.stderr, flush=True)

    elapsed = datetime.now() - start
    print(f"\nCompleted in {elapsed}", flush=True)

if __name__ == "__main__":
    main()
