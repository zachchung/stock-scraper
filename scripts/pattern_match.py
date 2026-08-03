import os
import sys
import datetime
import duckdb
import numpy as np

symbol = sys.argv[1].upper() if len(sys.argv) > 1 else "GOOGL"
window = 5
lookback_days = 365
top_n = 10
min_distance = None
metric = "keep"

for i, arg in enumerate(sys.argv):
    if arg == '--window' and i + 1 < len(sys.argv):
        window = int(sys.argv[i + 1])
    if arg == '--lookback' and i + 1 < len(sys.argv):
        lookback_days = int(sys.argv[i + 1])
    if arg == '--top' and i + 1 < len(sys.argv):
        top_n = int(sys.argv[i + 1])
    if arg == '--max-dist' and i + 1 < len(sys.argv):
        min_distance = float(sys.argv[i + 1])
    if arg == '--metric' and i + 1 < len(sys.argv):
        metric = sys.argv[i + 1]

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

con = duckdb.connect(f"{base_dir}/stocks.duckdb", read_only=True)
rows = con.execute(f"""
    SELECT date, close FROM ohlcv
    WHERE symbol = '{symbol}'
    ORDER BY date
""").fetchall()
con.close()

dates = [r[0] for r in rows]
closes = np.array([r[1] for r in rows], dtype=float)

last_date = dates[-1]
cutoff = last_date - datetime.timedelta(days=lookback_days)
start_idx = 0
for i, d in enumerate(dates):
    if d >= cutoff:
        start_idx = i
        break

def zscore(v):
    return (v - v.mean()) / (v.std() or 1.0)

def keepdir(v):
    return v / (v.std() or 1.0)

normalize = keepdir if metric == "keep" else zscore

recent_end = len(closes) - 1
recent_start = recent_end - window + 1
recent = closes[recent_start:recent_end + 1]
recent_ret = np.diff(recent) / recent[:-1]
recent_norm = normalize(recent_ret)
recent_total = recent[-1] / recent[0] - 1

def forward_returns(end_idx, horizons):
    out = {}
    for h in horizons:
        j = end_idx + h
        if j < len(closes):
            out[h] = closes[j] / closes[end_idx] - 1
        else:
            out[h] = None
    return out

horizons = [1, 5, 10]
matches = []
for end in range(start_idx + window - 1, recent_start):
    seg = closes[end - window + 1: end + 1]
    ret = np.diff(seg) / seg[:-1]
    n = normalize(ret)
    dist = float(np.sqrt(np.sum((n - recent_norm) ** 2)))
    total = seg[-1] / seg[0] - 1
    fwd = forward_returns(end, horizons)
    matches.append({
        "end": end,
        "end_date": dates[end],
        "start_date": dates[end - window + 1],
        "dist": dist,
        "ret": ret,
        "total": total,
        "fwd": fwd,
    })

matches.sort(key=lambda m: m["dist"])
if min_distance is not None:
    matches = [m for m in matches if m["dist"] <= min_distance]
selected = matches[:top_n]

def describe_fwd(rows, h):
    vals = [r["fwd"][h] for r in rows if r["fwd"][h] is not None]
    if not vals:
        return None
    arr = np.array(vals)
    return {"n": len(arr), "avg": arr.mean(), "med": np.median(arr),
            "win": (arr > 0).mean(), "min": arr.min(), "max": arr.max()}

print(f"== {symbol} {window}-day pattern match (metric: {metric}) ==")
print(f"Lookback: last {lookback_days} days ({dates[start_idx]} to {last_date})")
print()
print(f"Recent window: {dates[recent_start]} to {dates[recent_end]}")
print(f"  closes: {', '.join(f'{c:.2f}' for c in recent)}")
print(f"  daily returns: {', '.join(f'{r*100:+.2f}%' for r in recent_ret)}")
print(f"  {window}-day total: {recent_total*100:+.2f}%")
print()

def fmt(v):
    return f"{v*100:+.2f}%" if v is not None else "  n/a "

print(f"{'Rank':<5}{'Start':<12}{'End':<12}{'Dist':<8}{'5d ret':<10}{'Daily rets':<38}{'Fwd 1d':<9}{'Fwd 5d':<9}{'Fwd 10d':<9}")
print("-" * 108)
for rank, m in enumerate(selected, 1):
    daily = " ".join(f"{r*100:+.1f}" for r in m["ret"])
    print(f"#{rank:<4}{str(m['start_date']):<12}{str(m['end_date']):<12}"
          f"{m['dist']:<8.3f}{fmt(m['total']):<10}"
          f"{daily:<38}{fmt(m['fwd'][1]):<9}{fmt(m['fwd'][5]):<9}{fmt(m['fwd'][10]):<9}")
print()

print("== Forward returns after matched windows ==")
for h in horizons:
    s = describe_fwd(selected, h)
    if s is None:
        print(f"  +{h}d: no data")
        continue
    print(f"  +{h}d  n={s['n']}  avg={s['avg']*100:+.2f}%  median={s['med']*100:+.2f}%  "
          f"win={s['win']*100:.0f}%  range=[{s['min']*100:+.2f}%, {s['max']*100:+.2f}%]")

complete = [m for m in selected if m["fwd"][10] is not None]
print()
print("== Baseline: ALL windows in lookback ==")
for h in horizons:
    s = describe_fwd(matches, h)
    if s is None:
        continue
    print(f"  +{h}d  n={s['n']}  avg={s['avg']*100:+.2f}%  median={s['med']*100:+.2f}%  win={s['win']*100:.0f}%")
