"""Fully-online S&P500 upside screener - no local DB, no cache files on disk.

Everything is fetched from the network at runtime and kept only in memory.
To stay under yfinance rate limits, the work is staged from cheap to expensive:

  1. S&P500 universe + names  (datasets/s-and-p-500-companies GitHub CSV)
  2. S&P500 index YTD        (single ^GSPC download)
  3. market cap for ALL      (batched fast_info, chunked 25) -> keep only >= $100B
  4. YTD prices for large caps only (chunked, with retry/backoff)
  5. analyst targets via .info for survivors of the below-S&P500-YTD filter
     (few dozen tickers, chunked with sleep)
"""
import io
import sys
import time

import pandas as pd
import requests

SP500_CSV_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
GSPC = "^GSPC"
BASE_YEAR = 2025            # year whose last close acts as the YTD baseline
BASE_START = f"{BASE_YEAR}-12-15"
MCAP_FLOOR = 100e9
TOP_N = 100
CHUNK = 40
RETRIES = 3


def sp500_universe():
    resp = requests.get(SP500_CSV_URL, timeout=15)
    resp.raise_for_status()
    df = pd.read_csv(pd.io.common.StringIO(resp.text))
    df["Symbol"] = df["Symbol"].str.strip()
    name_col = "Security" if "Security" in df.columns else "Name"
    names = dict(zip(df["Symbol"], df[name_col].fillna("")))
    return df["Symbol"].tolist(), names


def close_series(data, sym):
    """Extract the Close series for one symbol from a yf.download result."""
    if isinstance(data.columns, pd.MultiIndex):
        col = (sym, "Close")
        if col in data.columns:
            return data[col].dropna()
        return None
    if "Close" in data.columns:
        return data["Close"].dropna()
    return None


def download(syms, start):
    """Chunked yf.download with retry/backoff. Returns {symbol: Close series}."""
    import yfinance as yf
    out = {}
    for i in range(0, len(syms), CHUNK):
        batch = syms[i:i + CHUNK]
        for attempt in range(RETRIES):
            try:
                data = yf.download(batch, start=start, auto_adjust=True,
                                   threads=True, progress=False, group_by="ticker")
                for s in batch:
                    cs = close_series(data, s)
                    if cs is not None and len(cs) > 0:
                        out[s] = cs
                break
            except Exception as e:
                print(f"download retry {attempt + 1}/{RETRIES} for {len(batch)} syms: {e}", file=sys.stderr)
                time.sleep(5 * (attempt + 1))
        time.sleep(1.5)
    return out


def all_market_caps(syms):
    import yfinance as yf
    mcap = {}
    for i in range(0, len(syms), 25):
        batch = syms[i:i + 25]
        try:
            tk = yf.Tickers(" ".join(batch))
            for t in tk.tickers.values():
                try:
                    mcap[t.ticker] = t.fast_info.market_cap
                except Exception:
                    mcap[t.ticker] = None
        except Exception as e:
            print(f"mcap chunk failed: {e}", file=sys.stderr)
        time.sleep(1.0)
    return mcap


def analyst_info(sym):
    import yfinance as yf
    for attempt in range(RETRIES):
        try:
            info = yf.Ticker(sym).info
            return {
                "tm": info.get("targetMeanPrice"),
                "num": info.get("numberOfAnalystOpinions"),
                "rating": info.get("recommendationKey"),
            }
        except Exception as e:
            if attempt < RETRIES - 1:
                print(f"info retry {attempt + 1}/{RETRIES} {sym}: {e}", file=sys.stderr)
                time.sleep(5 * (attempt + 1))
    return None


def main():
    syms, names = sp500_universe()
    print(f"universe: {len(syms)} symbols", file=sys.stderr)

    sp = download([GSPC], BASE_START).get(GSPC)
    if sp is None or len(sp) == 0:
        sys.exit("Could not fetch ^GSPC")
    sp_base_s = sp[sp.index <= f"{BASE_YEAR}-12-31"]
    sp_price = float(sp.iloc[-1])
    sp500_ytd = (sp_price / float(sp_base_s.iloc[-1]) - 1) * 100
    print(f"S&P500 YTD {sp500_ytd:.2f}% (base {float(sp_base_s.iloc[-1]):.2f} -> {sp_price:.2f} @ {sp.index[-1].date()})", file=sys.stderr)

    mcap = all_market_caps(syms)
    big = [s for s in syms if mcap.get(s) and not pd.isna(mcap[s]) and mcap[s] >= MCAP_FLOOR]
    print(f"market cap >= $100B: {len(big)}", file=sys.stderr)

    closes = download(big, BASE_START)

    survivors = []
    for s in big:
        cs = closes.get(s)
        if cs is None or len(cs) == 0:
            continue
        base = cs[cs.index <= f"{BASE_YEAR}-12-31"]
        if len(base) == 0:
            continue
        price = float(cs.iloc[-1])
        ytd = (price / float(base.iloc[-1]) - 1) * 100
        if ytd < sp500_ytd:
            survivors.append((s, ytd, price, mcap[s]))
    print(f"survivors (YTD below S&P500): {len(survivors)}", file=sys.stderr)

    rows = []
    for i, (s, ytd, price, mc) in enumerate(survivors):
        info = analyst_info(s)
        if info and info["tm"]:
            up = (float(info["tm"]) / price - 1) * 100
            rows.append([s, names.get(s, s), f"{mc / 1e9:.1f}B", f"{ytd:.1f}%",
                         f"{price:.2f}", f"{float(info['tm']):.2f}", f"{up:.1f}%",
                         str(int(info["num"])) if info["num"] else "", info["rating"] or ""])
        if (i + 1) % 25 == 0:
            print(f"  analysts fetched {i + 1}/{len(survivors)}", file=sys.stderr)
        time.sleep(0.4)

    rows.sort(key=lambda r: float(r[6][:-1]), reverse=True)
    rows = rows[:TOP_N]

    hdr = ["Symbol", "Company", "market_cap", "YTD", "Price", "Mean Tgt", "Upside%", "Analysts", "Rating"]
    print("\t".join(hdr))
    for r in rows:
        print("\t".join(r))


if __name__ == "__main__":
    main()