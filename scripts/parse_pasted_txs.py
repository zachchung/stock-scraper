import sys, re

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

print("date,ticker,side,shares,price")
cur_ticker = ""
for line in sys.stdin:
    line = line.rstrip("\n")
    if not line.strip():
        continue
    p = line.split("\t")
    # Skip header/label rows (they carry literal column names, not a price).
    if (len(p) > 2 and "price" in p[2].lower()) or (len(p) > 9 and "price" in p[9].lower()):
        continue
    # Carry-forward the ticker from this row's own ticker cell when present.
    b_ticker = p[0].strip().upper() if len(p) > 0 else ""
    s_ticker = p[7].strip().upper() if len(p) > 7 else ""
    tkr = b_ticker if valid_ticker(b_ticker) else (s_ticker if valid_ticker(s_ticker) else "")
    if tkr:
        cur_ticker = tkr
    if not cur_ticker:
        continue  # no ticker known yet — drop
    # Buy side: valid only if it carries a price (col 2); else drop.
    b_price = clean(p[2]) if len(p) > 2 else ""
    if b_price and len(p) > 1 and p[1].strip().upper() == "BUY":
        b_sh = clean(p[3]) if len(p) > 3 else ""
        b_date = parse_date(p[5]) if len(p) > 5 else ""
        print(f"{b_date},{cur_ticker},BUY,{b_sh},{b_price}")
    # Sell side: valid only if it carries a price (col 9); else drop.
    s_price = clean(p[9]) if len(p) > 9 else ""
    if s_price and len(p) > 8 and p[8].strip().upper() == "SELL":
        s_sh = clean(p[10]) if len(p) > 10 else ""
        s_date = parse_date(p[12]) if len(p) > 12 else ""
        print(f"{s_date},{cur_ticker},SELL,{s_sh},{s_price}")
