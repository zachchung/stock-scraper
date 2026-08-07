import sys, re, csv, io, argparse, datetime

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,5}$")

def parse_date(s):
    s = s.strip()
    if not s:
        return ""
    # Already normalized YYYY-MM-DD
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{y:04d}-{mo:02d}-{d:02d}"
    # Slash-separated YY-M/D -> YYYY-MM-DD
    m = re.match(r"(\d{2})-(\d{1,2})/(\d{1,2})", s)
    if m:
        yy, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{2000+yy}-{mo:02d}-{d:02d}"
    # Dash-separated YY-M-D -> YYYY-MM-DD (year-first, e.g. 21-3-3 => 2021-03-03)
    m = re.match(r"(\d{2})-(\d{1,2})-(\d{1,2})", s)
    if m:
        yy, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{2000+yy}-{mo:02d}-{d:02d}"
    # Dash-separated DD-MM-YY -> YYYY-MM-DD
    m = re.match(r"(\d{1,2})-(\d{1,2})-(\d{2})", s)
    if m:
        d, mo, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{2000+yy}-{mo:02d}-{d:02d}"
    return s

def clean(s):
    return s.strip().replace(",", "")

def valid_ticker(s):
    return bool(TICKER_RE.match(s.strip().upper()))

import os

OUT_DIR = "/Users/ZacharyChung1/code/stock_scraper/output"
OUT_NAME = f"transactions_{datetime.datetime.now():%Y-%m-%d_%H%M%S}.csv"

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, OUT_NAME)

    input_path = "/Users/ZacharyChung1/code/stock_scraper/output/Stock Portfolio - snapshot.csv"
    if not os.path.exists(input_path):
        print(f"input file not found: {input_path}", file=sys.stderr)
        return
    raw = open(input_path, "r", encoding="utf-8", errors="replace").read()
    if not raw.strip():
        print("no input", file=sys.stderr)
        return
    sample = raw[:4096]
    # Auto-detect the separator: prefer the one that appears more.
    tab_rows = sample.count("\t")
    comma_rows = sample.count(",")
    delimiter = "\t" if tab_rows > comma_rows else ","
    reader = csv.reader(io.StringIO(raw), delimiter=delimiter)

    cur_ticker = ""
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "ticker", "side", "shares", "price"])
        for p in reader:
            # Keep only columns A-N (first 14), drop any extras.
            p = [c.strip() for c in p[:14]]
            if not any(p):
                continue
            # Skip header/label rows (they carry literal column names, not a price).
            if (len(p) > 2 and "price" in p[2].lower()) or (len(p) > 9 and "price" in p[9].lower()):
                continue
            # Carry-forward the ticker from this row's own ticker cell when present.
            b_ticker = p[0].upper() if len(p) > 0 else ""
            s_ticker = p[7].upper() if len(p) > 7 else ""
            tkr = b_ticker if valid_ticker(b_ticker) else (s_ticker if valid_ticker(s_ticker) else "")
            if tkr:
                cur_ticker = tkr
            if not cur_ticker:
                continue  # no ticker known yet — drop
            # Buy side: valid only if it carries a price (col 2); else drop.
            b_price = clean(p[2]) if len(p) > 2 else ""
            if b_price and len(p) > 1 and p[1].upper() == "BUY":
                b_sh = clean(p[3]) if len(p) > 3 else ""
                b_date = parse_date(p[5]) if len(p) > 5 else ""
                w.writerow([b_date, cur_ticker, "BUY", b_sh, b_price])
            # Sell side: valid only if it carries a price (col 9); else drop.
            s_price = clean(p[9]) if len(p) > 9 else ""
            if s_price and len(p) > 8 and p[8].upper() == "SELL":
                s_sh = clean(p[10]) if len(p) > 10 else ""
                s_date = parse_date(p[12]) if len(p) > 12 else ""
                w.writerow([s_date, cur_ticker, "SELL", s_sh, s_price])
    print(out_path)

if __name__ == "__main__":
    main()
