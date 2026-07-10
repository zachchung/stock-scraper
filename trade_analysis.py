import duckdb

con = duckdb.connect()
data = con.execute("""
    SELECT date, open, high, low, close
    FROM read_parquet('data/stocks/ohlcv/data/symbol=META/*.parquet')
    ORDER BY date
""").fetchdf()

rows_list = data.to_dict('records')
current_price = data['close'].iloc[-1]

def simulate(entry, exit):
    trades = []
    in_trade = False
    for i, row in enumerate(rows_list):
        d = row['date']
        lo = row['low']
        hi = row['high']
        if not in_trade:
            if i > 0 and rows_list[i-1]['close'] >= entry and lo <= entry:
                in_trade = True
                buy_date = d
                buy_low = entry
        else:
            if hi >= exit:
                in_trade = False
                sell_date = d
                sell_high = exit
                days_held = (sell_date - buy_date).days
                trades.append((str(buy_date)[:10], buy_low, str(sell_date)[:10], sell_high, days_held))
    return trades

entry_range_min = round(current_price * 0.90, 2)
entry_range_max = round(current_price * 1.10, 2)

best_count = -1
best_entry = None
best_exit = None
best_trades = []

# Finer step for entry: $0.25, finer step for width: 0.001 (0.1%)
entry_step = 0.25
entry_candidates = []
e = entry_range_min
while e <= entry_range_max:
    entry_candidates.append(round(e, 2))
    e += entry_step

for entry in entry_candidates:
    width = 0.051  # > 5%
    while width <= 0.25:
        exit_price = round(entry * (1 + width), 2)
        trades = simulate(entry, exit_price)
        count = len(trades)
        if count > best_count:
            best_count = count
            best_entry = entry
            best_exit = exit_price
            best_trades = trades
            best_width = width
        width += 0.001

# Refine around best area with even finer grid
refine_range = 2.0  # +/- $2
refine_step = 0.10  # $0.10 step
e_start = max(best_entry - refine_range, entry_range_min)
e_end = min(best_entry + refine_range, entry_range_max)
refine_entry_candidates = []
e = e_start
while e <= e_end:
    refine_entry_candidates.append(round(e, 2))
    e += refine_step

for entry in refine_entry_candidates:
    width_start = max(best_width - 0.01, 0.051)
    width_end = min(best_width + 0.01, 0.25)
    width = width_start
    while width <= width_end:
        exit_price = round(entry * (1 + width), 2)
        trades = simulate(entry, exit_price)
        count = len(trades)
        if count > best_count or (count == best_count and entry == best_entry and width == best_width):
            pass
        if count > best_count:
            best_count = count
            best_entry = entry
            best_exit = exit_price
            best_trades = trades
            best_width = width
        width += 0.0005

print(f"== META Best Range ==")
print(f"Current Price: ${current_price:.2f}")
print(f"Entry Price: ${best_entry:.2f}")
print(f"Exit Price: ${best_exit:.2f}")
print(f"Range Width: {(best_exit / best_entry - 1) * 100:.2f}%")
print(f"Entry vs Current: {((best_entry / current_price) - 1) * 100:.2f}%")
print(f"Completed Trades: {best_count}")
print()
print(f"{'Buy Date':<13} {'Buy Low':<10} {'Sell Date':<13} {'Sell High':<10} {'Days Held':<10} {'P/L per share':<14}")
print("-" * 72)
total_pl = 0
for bd, bl, sd, sh, dh in best_trades:
    pl = sh - bl
    total_pl += pl
    print(f"{bd:<13} ${bl:<7.2f} {sd:<13} ${sh:<7.2f} {dh:<10} ${pl:<7.2f}")
print("-" * 72)
print(f"{'Total P/L per share':>52} ${total_pl:<7.2f}")

con.close()
