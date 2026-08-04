import os
import sys
import duckdb
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

symbol = sys.argv[1].upper() if len(sys.argv) > 1 else "DIS"
years = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
swing_window = int(sys.argv[3]) if len(sys.argv) > 3 else 60
band_pct = float(sys.argv[4]) if len(sys.argv) > 4 else 0.03

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
con = duckdb.connect()
df = con.execute(f"""
    SELECT date, close
    FROM read_parquet('{base_dir}/data/stocks/ohlcv_daily/data/symbol={symbol}/*.parquet')
    ORDER BY date
""").fetchdf()
con.close()

cutoff = df["date"].max() - pd.Timedelta(days=int(years * 365.25))
df = df[df["date"] >= cutoff].reset_index(drop=True)

def find_swings(vals, window):
    swings = []
    n = len(vals)
    for i in range(window, n - window):
        lo, hi = i - window, i + window
        if vals[i] == vals[lo:hi + 1].min():
            swings.append(("low", i, vals[i]))
        if vals[i] == vals[lo:hi + 1].max():
            swings.append(("high", i, vals[i]))
    return swings

x = mdates.date2num(pd.to_datetime(df["date"]))
prices = df["close"].values.astype(float)
swings = find_swings(prices, swing_window)

# convert integer row index -> date (x) coordinate for each swing point
def to_x(p):
    return x[p]
lows = [(to_x(i), v) for t, i, v in swings if t == "low"]
highs = [(to_x(i), v) for t, i, v in swings if t == "high"]

def best_lines(points, band):
    candidates = []
    for a in range(len(points)):
        for b in range(a + 1, len(points)):
            (x1, y1), (x2, y2) = points[a], points[b]
            if abs(x2 - x1) < 20:
                continue
            m = (y2 - y1) / (x2 - x1)
            c = y1 - m * x1
            touches = 0
            max_dev = 0.0
            for (px, py) in points:
                yhat = m * px + c
                dev = abs(yhat - py) / py
                if dev <= band:
                    touches += 1
                    max_dev = max(max_dev, dev)
            if touches >= 3:
                candidates.append((touches, max_dev, m, c, x1, y1, x2, y2))
    candidates.sort(key=lambda t: (-t[0], t[1]))
    return candidates

band = band_pct
low_lines = best_lines(lows, band)
high_lines = best_lines(highs, band)

print(f"Symbol: {symbol} | Window: {years:.0f}y | Swing window: {swing_window}d | Band: {band*100:.1f}%")
print(f"Date range: {df['date'].iloc[0].date()} -> {df['date'].iloc[-1].date()} | Last close: ${df['close'].iloc[-1]:.2f}")
print()
print("SUPPORT (rising) trendlines - touches, max deviation, slope $/day, anchored:")
for t in low_lines[:4]:
    touches, mdev, m, c, x1, y1, x2, y2 = t
    print(f"  touches={touches} max_dev={mdev*100:.1f}% slope=${m:.4f}/day  ({mdates.num2date(x1).strftime('%Y-%m-%d')} ${y1:.2f} -> {mdates.num2date(x2).strftime('%Y-%m-%d')} ${y2:.2f})")
print("RESISTANCE (falling) trendlines:")
for t in high_lines[:4]:
    touches, mdev, m, c, x1, y1, x2, y2 = t
    print(f"  touches={touches} max_dev={mdev*100:.1f}% slope=${m:.4f}/day  ({mdates.num2date(x1).strftime('%Y-%m-%d')} ${y1:.2f} -> {mdates.num2date(x2).strftime('%Y-%m-%d')} ${y2:.2f})")

fig, ax = plt.subplots(figsize=(13, 7))
ax.plot(df["date"], df["close"], color="#1f77b4", lw=1.2, label=f"{symbol} Close")

low_pts = [(to_x(i), v) for t, i, v in swings if t == "low"]
high_pts = [(to_x(i), v) for t, i, v in swings if t == "high"]
if low_pts:
    ax.scatter([mdates.num2date(i) for i, _ in low_pts], [v for _, v in low_pts],
               color="#2ca02c", s=28, marker="^", zorder=5, label="Swing low")
if high_pts:
    ax.scatter([mdates.num2date(i) for i, _ in high_pts], [v for _, v in high_pts],
               color="#d62728", s=28, marker="v", zorder=5, label="Swing high")

xmin, xmax = mdates.date2num(df["date"].iloc[0]), mdates.date2num(df["date"].iloc[-1])
project_days = int(years * 365.25 * 0.1)
x_extend = xmax + project_days
for t in low_lines[:3]:
    touches, mdev, m, c, x1, y1, x2, y2 = t
    xs = np.array([x1, x_extend])
    ys = m * xs + c
    ax.plot(mdates.num2date(xs), ys, color="#2ca02c", lw=1.8, ls="--",
            label=f"Support ({touches} touches)")
for t in high_lines[:3]:
    touches, mdev, m, c, x1, y1, x2, y2 = t
    xs = np.array([x1, x_extend])
    ys = m * xs + c
    ax.plot(mdates.num2date(xs), ys, color="#d62728", lw=1.8, ls="--",
            label=f"Resistance ({touches} touches)")

ax.set_title(f"{symbol} Trendlines ({df['date'].iloc[0].date()} - {df['date'].iloc[-1].date()})")
ax.set_ylabel("Price ($)")
ax.set_xlim(mdates.num2date(xmin), mdates.num2date(x_extend))
ax.legend(loc="upper left", fontsize=8)
ax.grid(alpha=0.3)
plt.tight_layout()
out = os.path.join(base_dir, f"trendlines_{symbol}.png")
plt.savefig(out, dpi=150)
print()
print(f"Saved chart: {out}")
