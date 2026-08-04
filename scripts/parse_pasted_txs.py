import sys, re

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,5}$")

def parse_date(s):
    s = s.strip()
    if not s:
        return ""
    m = re.match(r"(\d{2})-(\d{1,2})/(\d{1,2})", s)
    if m:
        yy, mm, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{2000+yy}-{mm:02d}-{dd:02d}"
    return s

def clean(s):
    return s.strip().replace(",", "")

def valid_ticker(s):
    return bool(TICKER_RE.match(s.strip().upper()))

print("date,ticker,side,shares,price")
for line in sys.stdin:
    line = line.rstrip("\n")
    if not line.strip():
        continue
    p = line.split("\t")
    if len(p) < 7:
        continue
    # Buy: 0 ticker,1 side,2 price,3 shares,4 amount,5 date,6 ignore
    b_ticker = p[0].strip().upper()
    if p[1].strip().upper() != "BUY":
        continue  # header line or malformed row
    # Sell: 7 ticker,8 side,9 price,10 shares,11 amount,12 date,13 ignore
    s_ticker = p[7].strip().upper() if len(p) > 7 else ""
    ticker = b_ticker if valid_ticker(b_ticker) else (s_ticker if valid_ticker(s_ticker) else "")
    if not ticker:
        continue  # header line, separator row, or section label
    b_price = clean(p[2]); b_sh = clean(p[3]); b_date = parse_date(p[5])
    s_price = clean(p[9]) if len(p) > 9 else ""
    s_sh = clean(p[10]) if len(p) > 10 else ""
    s_date = parse_date(p[12]) if len(p) > 12 else ""
    if b_date and b_sh and b_price:
        print(f"{b_date},{ticker},BUY,{b_sh},{b_price}")
    if s_date and s_sh and s_price:
        print(f"{s_date},{ticker},SELL,{s_sh},{s_price}")
