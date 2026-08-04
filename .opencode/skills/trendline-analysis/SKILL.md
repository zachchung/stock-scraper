---
name: trendline-analysis
description: Use when the user asks to identify, plot, or visualize trendlines (support/resistance lines) for a stock ticker such as DIS, MCD, HD, AAPL using the local stock_scraper dataset and the trendline_analysis.py script. Covers swing-point detection, fitting support/resistance trendlines, and IMPORTANTLY projecting/extending them to the right. DO NOT use for general stock questions or financial advice outside trendline/chart analysis.
---

# Trendline Analysis

Identifies support/resistance trendlines from price history in the local
stock_scraper dataset and plots them with an OHLC close chart.

## Script location

```
scripts/trendline_analysis.py
```

Base directory for this skill:
`/Users/ZacharyChung1/code/stock_scraper/.opencode/skills/trendline-analysis`
The script lives at `../../../scripts/trendline_analysis.py` relative to this
skill directory.

## Usage

Run from the `stock_scraper` base directory:

```bash
.venv/bin/python scripts/trendline_analysis.py <SYMBOL> [years] [swing_window] [band_pct]
```

Arguments (all optional after the symbol):

| Arg          | Default | Meaning                                                           |
| ------------ | ------- | ----------------------------------------------------------------- |
| `<SYMBOL>`   | DIS     | Ticker, e.g. DIS, MCD, HD, AAPL                                   |
| `years`      | 5       | Years of history to include (e.g. 1, 3, 5, 10)                    |
| `swing_window` | 60    | Trading days (±) for swing high/low detection (30 short, 120 long)|
| `band_pct`   | 0.03    | Max price deviation (as a fraction) to count as a trendline touch (0.02–0.05) |

The `stock_scraper` base directory is
`/Users/ZacharyChung1/code/stock_scraper` (the script resolves the data path
from its own location, so it works from anywhere).

## How it works

1. Load daily close prices from
   `data/stocks/ohlcv_daily/data/symbol=<SYMBOL>/*.parquet` via DuckDB.
2. Truncate to the last `years` of data.
3. Detect **swing highs** and **swing lows** using a local-extrema window of
   `swing_window` trading days (a day is a swing if it is the max/min of its
   full ±window neighborhood).
4. For each pair of same-type swing points, fit a line and count how many other
   swing points of that type fall within `band_pct` of it. Keep lines with
   >= 3 touches, ranked by most touches then tightest fit.
5. Print the top support (through lows) and resistance (through highs) lines
   with touches, deviation, and slope (#/day).
6. **Extend every trendline to the right** — from its first anchor through the
   last data point and ~10% of the lookback window (e.g. 6 months) into the
   future — and set the x-axis to include the projection.

## IMPORTANT: always extend to the right

When reporting/plotting trendlines, the lines MUST be projected forward into
the future (to the right of the chart). This is the key requirement. The
script already does this via `x_extend = xmax + project_days`. When you show
results, state the projected level of each line at the current date.

## Report format

After running, present:

- Current price and date range.
- Support lines (anchors, slope, and their projected level today).
- Resistance lines (anchors, slope, and their projected level today).
- A short read: what the price is sitting on, what a break above/below implies.

The script saves the chart to
`<stock_scraper>/trendlines_<SYMBOL>.png`. Note: view it via the Read tool;
tuning knobs are the lookback `years`, `swing_window`, and `band_pct` (widen
the band / shorten the window to find more lines).

## Example

```bash
.venv/bin/python scripts/trendline_analysis.py DIS 5 60 0.03
```

This produced DIS support near $96 (rising) with a ~$124 flat resistance.