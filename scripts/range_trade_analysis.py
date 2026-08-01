import os
import sys
import duckdb

symbol = sys.argv[1].upper() if len(sys.argv) > 1 else "META"

top_n = None
widths = [0.05, 0.10]
for i, arg in enumerate(sys.argv):
    if arg == '--top' and i + 1 < len(sys.argv):
        top_n = int(sys.argv[i + 1])
    if arg == '--widths' and i + 1 < len(sys.argv):
        widths = [float(w) for w in sys.argv[i + 1].split(',')]

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

con = duckdb.connect()
data = con.execute(f"""
    SELECT date, open, high, low, close
    FROM read_parquet('{base_dir}/data/stocks/ohlcv_daily/data/symbol={symbol}/*.parquet')
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

entry_step = 0.25
entry_candidates = []
e = entry_range_min
while e <= entry_range_max:
    entry_candidates.append(round(e, 2))
    e += entry_step

def print_trades(label, trades, entry, exit_price, width, count):
    print(f"== {label} ==")
    print(f"Current Price: ${current_price:.2f}")
    print(f"Entry Price: ${entry:.2f}")
    print(f"Exit Price: ${exit_price:.2f}")
    print(f"Range Width: {(exit_price / entry - 1) * 100:.2f}%")
    print(f"Entry vs Current: {((entry / current_price) - 1) * 100:.2f}%")
    print(f"Completed Trades: {count}")
    print()
    print(f"{'Buy Date':<13} {'Buy Low':<10} {'Sell Date':<13} {'Sell High':<10} {'Days Held':<10} {'P/L per share':<14}")
    print("-" * 72)
    total_pl = 0
    for bd, bl, sd, sh, dh in trades:
        pl = sh - bl
        total_pl += pl
        print(f"{bd:<13} ${bl:<7.2f} {sd:<13} ${sh:<7.2f} {dh:<10} ${pl:<7.2f}")
    print("-" * 72)
    print(f"{'Total P/L per share':>52} ${total_pl:<7.2f}")

def select_top_for_width(width, n):
    results = []
    for entry in entry_candidates:
        exit_price = round(entry * (1 + width), 2)
        trades = simulate(entry, exit_price)
        results.append((len(trades), entry, exit_price, trades))

    results.sort(key=lambda x: (-x[0], x[1], x[2]))

    selected = []
    selected_entries = []
    for count, entry, exit_price, trades in results:
        if not selected_entries:
            selected.append((count, entry, exit_price, trades))
            selected_entries.append(entry)
        else:
            too_close = any(abs(entry - se) / current_price < 0.05 for se in selected_entries)
            if not too_close:
                selected.append((count, entry, exit_price, trades))
                selected_entries.append(entry)
        if len(selected) == n:
            break
    return selected


if top_n:
    for width in widths:
        print(f"########## Width {width * 100:.1f}% ##########")
        print()
        for rank, (count, entry, exit_price, trades) in enumerate(select_top_for_width(width, top_n), 1):
            print_trades(f"#{rank} {symbol} Range", trades, entry, exit_price, width, count)
            print()
else:
    for width in widths:
        best_count = -1
        best_entry = None
        best_exit = None
        best_trades = []

        for entry in entry_candidates:
            exit_price = round(entry * (1 + width), 2)
            trades = simulate(entry, exit_price)
            count = len(trades)
            if count > best_count:
                best_count = count
                best_entry = entry
                best_exit = exit_price
                best_trades = trades

        print(f"########## Width {width * 100:.1f}% ##########")
        print()
        print_trades(f"{symbol} Best Range", best_trades, best_entry, best_exit, width, best_count)
        print()

con.close()
