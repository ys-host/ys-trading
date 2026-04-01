#!/usr/bin/env python3
"""
YS TRADING — NSE SCANNER ENGINE
Uses Yahoo Finance (confirmed working server-side)
Runs every 2 minutes via GitHub Actions during market hours
Replicates your Chartink Scanner 1 (Bullish) and Scanner 2 (Bearish) conditions
Saves results to Supabase → Dashboard shows ranked picks in real time
"""

import os, json, time, requests
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── CONFIG ────────────────────────────────────────────────────
SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')

IST = timezone(timedelta(hours=5, minutes=30))

NSE_HOLIDAYS = {
    '2026-01-26','2026-03-20','2026-04-02','2026-04-03',
    '2026-04-14','2026-05-01','2026-08-15','2026-10-02',
    '2026-11-14','2026-12-25',
    '2025-01-26','2025-02-26','2025-03-14','2025-03-31',
    '2025-04-10','2025-04-14','2025-04-18','2025-05-01',
    '2025-08-15','2025-08-27','2025-10-02','2025-10-21',
    '2025-10-22','2025-11-05','2025-12-25'
}

# ── NIFTY 500 SYMBOLS (Yahoo Finance format = SYM.NS) ─────────
# These are your universe — same stocks Chartink scans
NIFTY500_SYMS = [
    "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","HINDUNILVR","SBIN","BHARTIARTL",
    "ITC","KOTAKBANK","AXISBANK","LT","WIPRO","HCLTECH","ASIANPAINT","MARUTI",
    "ULTRACEMCO","BAJFINANCE","TATAMOTORS","ONGC","NTPC","POWERGRID","TECHM",
    "SUNPHARMA","DRREDDY","CIPLA","DIVISLAB","BAJAJFINSV","TITAN","NESTLEIND",
    "JSWSTEEL","TATASTEEL","HINDALCO","VEDL","COALINDIA","TATAPOWER","ADANIENT",
    "ADANIPORTS","GRASIM","EICHERMOT","HEROMOTOCO","M&M","BAJAJ-AUTO","TATACONSUM",
    "BRITANNIA","GODREJCP","DABUR","SAIL","NMDC","HINDPETRO","BPCL","IOC","CGPOWER",
    "BHEL","ABB","SIEMENS","BANDHANBNK","IDFCFIRSTB","FEDERALBNK","INDUSINDBK",
    "BIOCON","AUROPHARMA","LUPIN","GLENMARK","ALKEM","TORNTPHARM","IPCALAB",
    "GAIL","IGL","MGL","JINDALSTEE","HINDCOPPER","NATIONALUM","SBICARD","CHOLAFIN",
    "MUTHOOTFIN","RECLTD","PFC","VIKRAMSOLR","IRFC","ADANIGREEN","SUZLON",
    "TATAELXSI","LTIM","MPHASIS","PERSISTENT","COFORGE","KPITTECH","OFSS",
    "PIIND","ABBOTINDIA","SANOFI","PFIZER","GLAXO","LALPATHLAB","METROPOLIS",
    "APOLLOHOSP","FORTIS","MAXHEALTH","NAUKRI","ZOMATO","SWIGGY","PAYTM","POLICYBZR",
    "DELHIVERY","MAPMYINDIA","CARTRADE","INDIAMART","JUSTDIAL","INDIGRID",
    "POWERINDIA","KAJARIACER","CENTURYPLY","GREENPLY","CERA","HATSUN","HERITAGE",
    "VENKEYS","BIKAJI","DEVYANI","SAPPHIRE","WESTLIFE","JUBLFOOD","BARBEQUE",
    "RADICO","UNITEDSPRT","TILAKNAGAR","MCDOWELL-N","SULA","VSTIND",
    "PAGEIND","VEDANT","RAYMOND","ADITBIRLAF","MAFANG","CAMPUS","METRO","BATA",
    "RELAXO","SREINFRA","KPRMILL","WELSPUN","TRIDENT","VARDHMAN","NIITLTD",
    "CAMS","CDSL","BSE","MCX","ANGELONE","IIFLWAM","MOTILALOFS","MOFSL",
    "360ONE","NUVAMA","EDELWEISS","IIFL","INDIABULL","IBULHSGFIN","PIRAMALENT",
    "LICHSGFIN","BAJAJHFL","APTUS","HOMEFIRST","AAVAS","REPCO","CANFINHOME",
    "HDFCLIFE","SBILIFE","ICICIPRULI","ABSLAMC","UTIAMC","NIPPONLIFE","HDFCAMC",
    "GICRE","NIACL","STARHEALTH","NIACL","MAXFIN",
    "ZEEL","SUNTV","NETWORK18","TV18BRDCST","JAGRAN","DBCORP","HT-MEDIA",
    "GMRAIRPORT","AIAENG","THERMAX","CUMMINSIND","GREAVES","ELGIEQUIP","KSB",
    "TIMKEN","SKF","SCHAEFFLER","NRB","SWARAJENG","VST","TRACTORS","MAHINDCIE",
    "SUNDRMFAST","SUPRAJIT","MINDA","MOTHERSON","BOSCHLTD","EXIDEIND","AMARA",
    "TVSMOTOR","BAJAJ-AUTO","HEROMOTOCO","EICHERMOT","ESCORTS","FORCE","SML",
    "ASHOKLEY","TIINDIA","BHARATFORG","RAMKRISHNA","JSWENERGY","CESC","TORNTPOWER",
    "ADANIPOWER","RPOWER","NHPC","SJVN","IRCON","RVNL","RAIL","RAILTEL","NMDC",
    "MOIL","GMDC","NDBCL","WELCORP","APL","RATNAMANI","RAMASTEEL","SALASAR",
    "MANAPPURAM","EQUITASBNK","UJJIVAN","SURYODAY","ESAFSFB","FINOPB","UTKARSH",
    "CREDITACC","SPANDANA","ARMANFIN","UGROCAP","VERITAS","PAISALO",
    "KFINTECH","MASFIN","CAPACITE","VRL","MAHLOG","BLUEDART","GATI","AEGIS",
    "CONCOR","GATEWAY","ALLCARGO","MAHSEAMLES","WELSPUNLIV","HIMATSEIDE",
    "ORIENTELEC","HAVELLS","POLYCAB","KEI","FINOLEX","HBLPOWER","AMETEK",
    "DIXON","AMBER","PGEL","KAYNES","SYRMA","AVALON","ELIN","CENTUM",
    "MASTEK","BIRLASOFT","HEXAWARE","TANLA","LATENTVIEW","INTELLECT","NEWGEN",
    "ROUTE","RATEGAIN","ZAGGLE","NUVOCO","HEIDELBERG","BIRLACORPN","JKCEMENT",
    "SHREECEM","ACC","AMBUJACEMENT","RAMCOCEM","INDIACEM","DALMIA","PRISMJOH",
    "GUJGASLTD","PETRONET","AEGASCHEM","CLEAN","MFSL","SUNTECK","KOLTEPATIL",
    "SOBHA","BRIGADE","PRESTIGE","DLF","GODREJPROP","OBEROIRLTY","MACROTECH",
    "PHOENIXLTD","INORBIT","NESCO","EQUITASBNK","UJJIVANSF","FINOPB",
    "RAINBOW","KRSNAA","VIJAYA","ASTERDM","YATHARTH","SHALBY",
    "TEJASNET","HFCL","STERLITE","VINDHYATEL","ITI","RAILTEL","EASEMYTRIP",
    "IXIGO","YATRA","TOURPKG","THOMASCOOK","MHRIL","CHALET","JUNIPER",
    "LEMONTREE","EIHOTEL","MAHINDHOTEL","TAJGVK","ORIENTHOTEL",
]

# Remove duplicates
NIFTY500_SYMS = list(dict.fromkeys(NIFTY500_SYMS))
print(f"Universe: {len(NIFTY500_SYMS)} stocks")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json',
}

# ── TIME CHECKS ───────────────────────────────────────────────
def get_ist():
    now = datetime.now(IST)
    return {
        'date': now.strftime('%Y-%m-%d'),
        'time': now.strftime('%H:%M:%S'),
        'mins': now.hour * 60 + now.minute,
        'day':  now.weekday(),  # 0=Mon, 6=Sun
        'dt':   now,
    }

def is_trading_time(ist):
    if ist['day'] >= 5:  # Saturday or Sunday
        return False, 'Weekend'
    if ist['date'] in NSE_HOLIDAYS:
        return False, 'NSE Holiday'
    m = ist['mins']
    if m < 9*60+15:
        return False, 'Pre-market'
    if m > 11*60+30:
        return False, 'Market closed after 11:30'
    return True, 'Market open'

# ── YAHOO FINANCE FETCH ───────────────────────────────────────
def fetch_stock_candles(sym):
    """Fetch last 20 five-minute candles for one stock from Yahoo Finance."""
    ticker = sym + '.NS'
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{ticker}'
    params = {
        'interval': '5m',
        'range': '1d',
        'includePrePost': 'false',
    }
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        result = data.get('chart', {}).get('result', [])
        if not result:
            return None
        res = result[0]
        meta = res.get('meta', {})
        quotes = res.get('indicators', {}).get('quote', [{}])[0]
        timestamps = res.get('timestamp', [])

        opens   = quotes.get('open', [])
        highs   = quotes.get('high', [])
        lows    = quotes.get('low', [])
        closes  = quotes.get('close', [])
        volumes = quotes.get('volume', [])

        if len(closes) < 3:
            return None

        # Filter out None values
        valid = [(o,h,l,c,v,t) for o,h,l,c,v,t
                 in zip(opens,highs,lows,closes,volumes,timestamps)
                 if all(x is not None for x in [o,h,l,c,v])]

        if len(valid) < 3:
            return None

        return {
            'sym': sym,
            'name': meta.get('longName', sym),
            'prev_close': meta.get('previousClose', 0) or meta.get('chartPreviousClose', 0),
            'candles': valid,  # list of (open,high,low,close,volume,timestamp)
            'currency': meta.get('currency', 'INR'),
        }
    except Exception:
        return None

# ── SCANNER CONDITIONS ────────────────────────────────────────
def vol_sma(candles, periods=20):
    """Average volume of last N candles."""
    vols = [c[4] for c in candles[-periods:] if c[4]]
    return sum(vols) / len(vols) if vols else 0

def run_scanner(stock_data):
    """
    Apply your scanner conditions to one stock.
    Returns: ('S1', stock_info) or ('S2', stock_info) or None
    """
    if not stock_data:
        return None

    sym      = stock_data['sym']
    candles  = stock_data['candles']
    prev_cls = stock_data['prev_close']

    if len(candles) < 3 or not prev_cls:
        return None

    # Current and previous candle
    curr = candles[-1]   # (open,high,low,close,volume,ts)
    prev = candles[-2]

    c_open, c_high, c_low, c_close, c_vol, _ = curr
    p_open, p_high, p_low, p_close, p_vol, _ = prev

    if not all([c_open, c_high, c_low, c_close, c_vol, prev_cls]):
        return None

    # Basic price filters
    if c_close < 100:          # Price below ₹100 → skip
        return None

    # Volume SMA (last 20 five-min candles)
    avg_vol = vol_sma(candles, 20)
    if avg_vol <= 0:
        return None

    vol_ratio   = c_vol / avg_vol
    gap_pct     = (c_open - prev_cls) / prev_cls * 100
    range_pct   = (c_high - c_low) / c_close * 100
    chg_pct     = (c_close - prev_cls) / prev_cls * 100

    # ── SCANNER 1: BULLISH ────────────────────────────────────
    # Condition 1: Gap filter — open within 1.5% of prev close
    # Condition 2: Bullish candle (close > open)
    # Condition 3: Close above previous candle HIGH (momentum)
    # Condition 4: Volume > 2× SMA (institutional)
    # Condition 5: Volume < 6× SMA (not panic)
    # Condition 6: Range < 3% (controlled move)
    # Condition 7: Not more than 4% above prev close
    s1 = (
        abs(gap_pct)   <= 1.5  and   # gap filter
        c_close        >  c_open and  # bullish candle
        c_close        >  p_high and  # above prev high
        vol_ratio      >  2.0   and   # volume surge
        vol_ratio      <  6.0   and   # not panic
        range_pct      <  3.0   and   # tight candle
        chg_pct        <  4.0         # not already run up
    )

    # ── SCANNER 2: BEARISH ────────────────────────────────────
    # Bearish version of the same logic
    s2 = (
        gap_pct        >= -3.0  and   # allow gap down up to 3%
        gap_pct        <= 0.5   and   # not a gap up stock
        c_close        <  c_open and  # bearish candle
        c_close        <  p_low  and  # below prev low
        vol_ratio      >  2.0   and   # volume surge
        vol_ratio      <  6.0   and   # not panic
        range_pct      <  3.0   and   # tight candle
        chg_pct        >  -6.0        # not already crashed
    )

    if s1:
        return ('S1', {
            'sym':      sym,
            'name':     stock_data['name'],
            'scanner':  1,
            'close':    round(c_close, 2),
            'chg_pct':  round(chg_pct, 2),
            'vol':      int(c_vol),
            'vol_ratio':round(vol_ratio, 1),
            'gap_pct':  round(gap_pct, 2),
        })
    elif s2:
        return ('S2', {
            'sym':      sym,
            'name':     stock_data['name'],
            'scanner':  2,
            'close':    round(c_close, 2),
            'chg_pct':  round(chg_pct, 2),
            'vol':      int(c_vol),
            'vol_ratio':round(vol_ratio, 1),
            'gap_pct':  round(gap_pct, 2),
        })
    return None

# ── SUPABASE SAVE ─────────────────────────────────────────────
def save_to_supabase(rows, table):
    if not rows or not SUPABASE_URL or not SUPABASE_KEY:
        return False
    headers = {
        'Content-Type': 'application/json',
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Prefer': 'return=minimal'
    }
    try:
        r = requests.post(
            f'{SUPABASE_URL}/rest/v1/{table}',
            headers=headers,
            json=rows,
            timeout=20
        )
        return r.status_code in (200, 201)
    except Exception as e:
        print(f'  Supabase error: {e}')
        return False

def upsert_to_supabase(rows, table, conflict):
    if not rows or not SUPABASE_URL or not SUPABASE_KEY:
        return False
    headers = {
        'Content-Type': 'application/json',
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Prefer': f'resolution=merge-duplicates,return=minimal',
        'on-conflict': conflict
    }
    try:
        r = requests.post(
            f'{SUPABASE_URL}/rest/v1/{table}',
            headers=headers,
            json=rows,
            timeout=20
        )
        return r.status_code in (200, 201)
    except Exception as e:
        print(f'  Supabase upsert error: {e}')
        return False

def get_from_supabase(table, filters):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    headers = {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}'
    }
    params = {'select': '*', **filters}
    try:
        r = requests.get(
            f'{SUPABASE_URL}/rest/v1/{table}',
            headers=headers,
            params=params,
            timeout=15
        )
        return r.json() if r.status_code == 200 else []
    except:
        return []

def compute_score(freq, streak, dpct, chg, vol, gf):
    s  = freq * 25
    s += streak * 15 if streak >= 3 else 0
    s += dpct * 0.5
    s += gf * 10
    s += abs(chg) * 3
    s += min((vol / 1e5) * 0.5, 25)
    return round(s * 10) / 10

def compute_streak(times):
    if not times:
        return 0
    sorted_t = sorted(times)
    cur = max_s = 1
    last = -999
    for t in sorted_t:
        h, m = int(t[:2]), int(t[3:5])
        mins = h * 60 + m
        if last == -999:
            cur = 1
        elif mins - last <= 7:
            cur += 1
            max_s = max(max_s, cur)
        else:
            cur = 1
        last = mins
    return max_s

def rebuild_frequency(date, scanner_num):
    """Recompute daily_frequency from all scan_runs for today."""
    rows = get_from_supabase('scan_runs', {
        'trade_date': f'eq.{date}',
        'scanner': f'eq.{scanner_num}',
        'order': 'scan_time.asc'
    })
    if not rows:
        return 0

    # Get bhavcopy delivery %
    bhav = get_from_supabase('bhavcopy', {'trade_date': f'eq.{date}', 'select': 'symbol,delivery_pct'})
    bhav_map = {b['symbol']: b.get('delivery_pct', 0) for b in bhav}

    # Get gainer frequency
    gainers = get_from_supabase('nse_live', {'trade_date': f'eq.{date}', 'select': 'symbol'})
    gainer_count = {}
    for g in gainers:
        gainer_count[g['symbol']] = gainer_count.get(g['symbol'], 0) + 1

    # Build frequency map
    sym_map = {}
    for row in rows:
        sym = row['symbol']
        if sym not in sym_map:
            sym_map[sym] = {'times': [], 'name': row.get('stock_name',''), 'close': 0, 'chg': 0, 'vol': 0}
        sym_map[sym]['times'].append(row['scan_time'])
        if row.get('close_price'):  sym_map[sym]['close'] = row['close_price']
        if row.get('change_pct'):   sym_map[sym]['chg']   = row['change_pct']
        if row.get('volume'):       sym_map[sym]['vol']   = max(sym_map[sym]['vol'], row['volume'])
        if row.get('stock_name'):   sym_map[sym]['name']  = row['stock_name']

    upserts = []
    for sym, info in sym_map.items():
        freq   = len(info['times'])
        streak = compute_streak(info['times'])
        dpct   = bhav_map.get(sym, 0)
        gf     = gainer_count.get(sym, 0)
        score  = compute_score(freq, streak, dpct, info['chg'], info['vol'], gf)
        upserts.append({
            'trade_date':  date,
            'symbol':      sym,
            'stock_name':  info['name'],
            'scanner':     scanner_num,
            'freq_count':  freq,
            'streak':      streak,
            'max_streak':  streak,
            'first_seen':  info['times'][0],
            'last_seen':   info['times'][-1],
            'close_price': info['close'],
            'change_pct':  info['chg'],
            'volume':      info['vol'],
            'delivery_pct':dpct,
            'gainer_freq': gf,
            'score':       score,
            'sector':      'Others',
            'updated_at':  datetime.now(IST).isoformat(),
        })

    if upserts:
        upsert_to_supabase(upserts, 'daily_frequency', 'trade_date,symbol,scanner')

    return len(upserts)

# ── MAIN ──────────────────────────────────────────────────────
def main():
    ist = get_ist()
    print(f"\n{'='*60}")
    print(f"YS Scanner — {ist['date']} {ist['time']} IST")
    print(f"{'='*60}")

    ok, reason = is_trading_time(ist)
    if not ok:
        print(f"Skipping: {reason}")
        return

    print(f"Market open — scanning {len(NIFTY500_SYMS)} stocks...")
    t_start = time.time()

    # Fetch all stocks in parallel (20 workers)
    results_s1, results_s2 = [], []
    fetched, scanned = 0, 0

    def process(sym):
        data = fetch_stock_candles(sym)
        if data:
            return run_scanner(data)
        return None

    with ThreadPoolExecutor(max_workers=20) as ex:
        futures = {ex.submit(process, sym): sym for sym in NIFTY500_SYMS}
        for future in as_completed(futures):
            result = future.result()
            fetched += 1
            if result:
                scanned += 1
                tag, info = result
                if tag == 'S1':
                    results_s1.append(info)
                else:
                    results_s2.append(info)

    elapsed = time.time() - t_start
    print(f"Fetched {fetched} stocks in {elapsed:.1f}s")
    print(f"S1 (Bullish): {len(results_s1)} stocks")
    print(f"S2 (Bearish): {len(results_s2)} stocks")

    # Print matches
    if results_s1:
        print("\nS1 Matches:")
        for s in sorted(results_s1, key=lambda x: -x['vol_ratio']):
            print(f"  {s['sym']:15} +{s['chg_pct']:.2f}%  Vol:{s['vol_ratio']:.1f}x  Close:{s['close']}")

    if results_s2:
        print("\nS2 Matches:")
        for s in sorted(results_s2, key=lambda x: -x['vol_ratio']):
            print(f"  {s['sym']:15} {s['chg_pct']:.2f}%  Vol:{s['vol_ratio']:.1f}x  Close:{s['close']}")

    # Save to Supabase
    if SUPABASE_URL and SUPABASE_KEY:
        all_matches = results_s1 + results_s2
        if all_matches:
            scan_rows = [{
                'trade_date':  ist['date'],
                'scan_time':   ist['time'],
                'scanner':     s['scanner'],
                'symbol':      s['sym'],
                'stock_name':  s['name'],
                'close_price': s['close'],
                'change_pct':  s['chg_pct'],
                'volume':      s['vol'],
            } for s in all_matches]

            ok1 = save_to_supabase(scan_rows, 'scan_runs')
            print(f"\nSaved to scan_runs: {'✅' if ok1 else '❌'} ({len(scan_rows)} rows)")

            # Rebuild frequency for both scanners
            for sc in [1, 2]:
                cnt = rebuild_frequency(ist['date'], sc)
                print(f"daily_frequency S{sc}: {cnt} stocks updated")
        else:
            print("\nNo matches this scan — market quiet or conditions not met")
    else:
        print("\n(No Supabase config — dry run only)")

    print(f"\nScan complete in {time.time()-t_start:.1f}s")

if __name__ == '__main__':
    main()
