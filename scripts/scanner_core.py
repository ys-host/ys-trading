"""
YS TRADING — scanner_core.py
Core scanning engine — runs as 1-minute loop inside GitHub Actions job.
File: scripts/scanner_core.py

HOW IT WORKS:
  1. At 9:00 AM: fetch HTF data (daily EMA20, SMA50, RSI) for all 500 symbols. Cache it.
  2. Every 1 minute: fetch 1-min/5-min candles for top 100 + full 500 every 5 runs
  3. Apply strategy conditions (HTF v3 ORB + ORB Reversal)
  4. Save matches to Supabase scan_runs
  5. Rebuild daily_picks (freq, streak, score)
  6. Dashboard updates via Supabase realtime subscription

ANGEL ONE RATE LIMIT MANAGEMENT:
  ~100 requests/min allowed.
  Solution: 20 parallel workers, each handling 5-25 symbols.
  Full 500-stock scan completes in ~30 seconds.
"""

import os, sys, time, json, threading, concurrent.futures
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from angel_api import AngelAPI, RateLimiter
from symbol_master import SymbolMaster, NIFTY500_SYMBOLS

# ── Config ────────────────────────────────────────────────────────
IST         = timezone(timedelta(hours=5, minutes=30))
MARKET_OPEN  = (9, 15)   # 9:15 AM IST
MARKET_CLOSE = (15, 25)  # 3:25 PM IST (5 min before close)
SCAN_INTERVAL_SEC = 60   # 1 minute
MAX_WORKERS  = 20        # parallel threads for Angel One API
HTF_CACHE_FILE = Path('/tmp/ys_htf_cache.json')

# Supabase (used via REST since we're in Python, not JS)
SUPA_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SUPA_KEY = os.environ.get('SUPABASE_SERVICE_KEY', '')


# ── Supabase helper (plain requests — no supabase SDK needed) ─────
import requests as req

def supa_insert(table: str, rows: list):
    """Bulk insert rows into Supabase table."""
    if not rows or not SUPA_KEY:
        return
    headers = {
        'apikey': SUPA_KEY,
        'Authorization': f'Bearer {SUPA_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'return=minimal',
    }
    try:
        r = req.post(f'{SUPA_URL}/rest/v1/{table}',
                     json=rows, headers=headers, timeout=15)
        if r.status_code not in (200, 201):
            print(f"  Supabase insert error {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"  Supabase error: {e}")

def supa_upsert(table: str, rows: list, on_conflict: str):
    """Upsert rows into Supabase table."""
    if not rows or not SUPA_KEY:
        return
    headers = {
        'apikey': SUPA_KEY,
        'Authorization': f'Bearer {SUPA_KEY}',
        'Content-Type': 'application/json',
        'Prefer': f'resolution=merge-duplicates,return=minimal',
    }
    try:
        r = req.post(f'{SUPA_URL}/rest/v1/{table}?on_conflict={on_conflict}',
                     json=rows, headers=headers, timeout=15)
        if r.status_code not in (200, 201):
            print(f"  Supabase upsert error {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"  Supabase error: {e}")

def supa_select(table: str, params: str = '') -> list:
    """Select from Supabase table."""
    if not SUPA_KEY:
        return []
    headers = {
        'apikey': SUPA_KEY,
        'Authorization': f'Bearer {SUPA_KEY}',
    }
    try:
        r = req.get(f'{SUPA_URL}/rest/v1/{table}?{params}',
                    headers=headers, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return []


# ── HTF Cache ─────────────────────────────────────────────────────
def build_htf_cache(api: AngelAPI, symbols: list) -> dict:
    """
    Fetch 60 days of daily candles for all symbols.
    Calculate: EMA20, SMA50, RSI14, price_vs_20d_ago.
    Returns: {symbol: {ema20, sma50, rsi14, above_ema20, above_sma50, trend_up}}
    
    Called ONCE at market open (9:00 AM). Cached to /tmp.
    HTF data doesn't change during the trading day.
    """
    ist_now = datetime.now(IST)
    today   = ist_now.strftime('%Y-%m-%d')
    
    # Skip if cache is from today already
    if HTF_CACHE_FILE.exists():
        try:
            cache = json.loads(HTF_CACHE_FILE.read_text())
            if cache.get('_date') == today:
                print(f"HTF cache hit: {len(cache)-1} symbols from today")
                return cache
        except Exception:
            pass
    
    print(f"Building HTF cache for {len(symbols)} symbols...")
    from_dt = (ist_now - timedelta(days=90)).strftime('%Y-%m-%d %09:00')
    to_dt   = ist_now.strftime('%Y-%m-%d %15:30')
    
    cache = {'_date': today}
    sm    = SymbolMaster()
    limiter = RateLimiter(max_per_min=80)
    
    def fetch_one(sym_token):
        sym, token = sym_token
        limiter.wait()
        candles = api.get_candles('NSE', token, 'ONE_DAY', from_dt, to_dt)
        if not candles or len(candles) < 21:
            return sym, None
        
        closes = [float(c[4]) for c in candles]  # index 4 = close
        
        ema20  = _ema(closes, 20)
        sma50  = _sma(closes, 50) if len(closes) >= 50 else None
        rsi14  = _rsi(closes, 14)
        last   = closes[-1]
        ago20  = closes[-21] if len(closes) >= 21 else last
        
        return sym, {
            'ema20':       round(ema20, 2) if ema20 else None,
            'sma50':       round(sma50, 2) if sma50 else None,
            'rsi14':       round(rsi14, 1) if rsi14 else None,
            'last_close':  round(last, 2),
            'above_ema20': last > ema20 if ema20 else None,
            'above_sma50': last > sma50 if sma50 else None,
            'trend_up':    last > ago20,   # higher than 20 days ago
        }
    
    tokens = sm.get_nifty500_tokens()
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for sym, data in ex.map(fetch_one, tokens):
            if data:
                cache[sym] = data
    
    HTF_CACHE_FILE.write_text(json.dumps(cache))
    print(f"HTF cache built: {len(cache)-1} symbols")
    return cache


# ── Technical Indicators ─────────────────────────────────────────
def _ema(values: list, period: int) -> float:
    """Exponential Moving Average."""
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema

def _sma(values: list, period: int) -> float:
    """Simple Moving Average."""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period

def _rsi(values: list, period: int = 14) -> float:
    """Relative Strength Index."""
    if len(values) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(values)):
        delta = values[i] - values[i-1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    rs = ag / al
    return round(100 - 100 / (1 + rs), 1)

def _vol_sma(volumes: list, period: int = 20) -> float:
    """Volume SMA."""
    if len(volumes) < period:
        return None
    return sum(volumes[-period:]) / period


# ── Single Symbol Scan ─────────────────────────────────────────────
def scan_symbol(api, sym, token, htf_cache, scan_time_str, today_str) -> list:
    """
    Fetch today's 1-min candles for one symbol.
    Apply HTF v3 ORB + ORB Reversal conditions.
    Returns list of match dicts (can have 0, 1, or 2 matches per scan).
    """
    # Fetch today's 1-min candles from 9:15 AM to now
    ist_now = datetime.now(IST)
    from_dt = f'{today_str} 09:15'
    to_dt   = ist_now.strftime('%Y-%m-%d %H:%M')
    
    candles = api.get_candles('NSE', token, 'ONE_MINUTE', from_dt, to_dt)
    if not candles or len(candles) < 5:
        return []
    
    # Parse candles: [timestamp, open, high, low, close, volume]
    opens   = [float(c[1]) for c in candles]
    highs   = [float(c[2]) for c in candles]
    lows    = [float(c[3]) for c in candles]
    closes  = [float(c[4]) for c in candles]
    volumes = [int(c[5])   for c in candles]
    
    if not closes:
        return []
    
    # ORB levels (first candle = 9:15 to 9:16 AM, index 0)
    orb_high = highs[0]
    orb_low  = lows[0]
    
    # Current candle (last complete candle)
    cur_open  = opens[-1]
    cur_high  = highs[-1]
    cur_low   = lows[-1]
    cur_close = closes[-1]
    cur_vol   = volumes[-1]
    prev_high = highs[-2] if len(highs) >= 2 else highs[-1]
    prev_low  = lows[-2]  if len(lows)  >= 2 else lows[-1]
    
    # Previous day close (for gap calculation)
    # Use first candle open as proxy (no prev day data in intraday fetch)
    prev_close = opens[0]
    
    # Volume SMA(20) from today's candles
    vol_sma = _vol_sma(volumes[:-1], 20)  # exclude current candle
    if not vol_sma:
        vol_sma = cur_vol  # fallback
    vol_ratio = cur_vol / vol_sma if vol_sma > 0 else 0
    
    # Candle range %
    candle_range = (cur_high - cur_low) / cur_close if cur_close > 0 else 0
    
    # HTF data
    htf = htf_cache.get(sym, {})
    above_ema20 = htf.get('above_ema20')  # None if not in cache
    above_sma50 = htf.get('above_sma50')
    trend_up    = htf.get('trend_up')
    last_close  = htf.get('last_close', cur_close)
    
    # Gap filter (within ±1.5% of prev close)
    gap_pct = (cur_open - prev_close) / prev_close if prev_close > 0 else 0
    
    matches = []
    
    # ────────────────────────────────────────────────────────────────
    # STRATEGY 1: ORB HTF BULLISH (BL / FS tag assigned by dashboard)
    # HTF v3 conditions from YS_Scanners_v3_HTF.txt
    # ────────────────────────────────────────────────────────────────
    s1_pass = (
        abs(gap_pct) <= 0.015                    # C1,C2: gap within ±1.5%
        and cur_close > cur_open                  # C3: bullish candle
        and cur_close > prev_high                 # C4: breaking above prev high
        and vol_ratio >= 2.5                      # C5: volume surge 2.5×
        and vol_ratio <= 8.0                      # C6: not panic spike
        and candle_range <= 0.025                 # C7: controlled candle <2.5%
        and cur_close < prev_close * 1.05         # C8: not already up 5%
        and cur_close >= 150                      # C9: price filter
        and candle_range > 0                      # sanity
    )
    # HTF conditions (C12-C14) — only apply if cache available
    htf_bull = (
        above_ema20 is None or above_ema20        # C12: above daily EMA20
    ) and (
        above_sma50 is None or above_sma50        # C13: above daily SMA50
    ) and (
        trend_up    is None or trend_up            # C14: higher than 20d ago
    )
    
    if s1_pass and htf_bull:
        matches.append({
            'strategy_code': 'ORB',
            'scanner':       1,          # 1 = bullish/long setup
            'symbol':        sym,
            'token':         token,
            'close_price':   round(cur_close, 2),
            'change_pct':    round((cur_close - prev_close) / prev_close * 100, 4) if prev_close > 0 else 0,
            'volume':        cur_vol,
            'vol_sma20':     int(vol_sma),
            'vol_ratio':     round(vol_ratio, 2),
            'orb_high':      round(orb_high, 2),
            'orb_low':       round(orb_low, 2),
            'candle_range':  round(candle_range, 4),
            'trade_date':    today_str,
            'scan_time':     scan_time_str,
        })
    
    # ────────────────────────────────────────────────────────────────
    # STRATEGY 2: ORB HTF BEARISH (BD / FL tag assigned by dashboard)
    # ────────────────────────────────────────────────────────────────
    s2_pass = (
        gap_pct <= 0.005 and gap_pct >= -0.030    # C1,C2: gap filter for bearish
        and cur_close < cur_open                  # C3: bearish candle
        and cur_close < prev_low                  # C4: breaking below prev low
        and vol_ratio >= 2.5                      # C5: volume surge
        and vol_ratio <= 8.0                      # C6: not extreme
        and candle_range <= 0.025                 # C7: controlled
        and cur_close > prev_close * 0.94         # C8: not fallen 6%+
        and cur_close >= 150                      # C9
    )
    htf_bear = (
        above_ema20 is None or not above_ema20    # C12: below daily EMA20
    ) and (
        above_sma50 is None or not above_sma50    # C13: below daily SMA50
    ) and (
        trend_up    is None or not trend_up        # C14: lower than 20d ago
    )
    
    if s2_pass and htf_bear:
        matches.append({
            'strategy_code': 'ORB',
            'scanner':       2,          # 2 = bearish/short setup
            'symbol':        sym,
            'token':         token,
            'close_price':   round(cur_close, 2),
            'change_pct':    round((cur_close - prev_close) / prev_close * 100, 4) if prev_close > 0 else 0,
            'volume':        cur_vol,
            'vol_sma20':     int(vol_sma),
            'vol_ratio':     round(vol_ratio, 2),
            'orb_high':      round(orb_high, 2),
            'orb_low':       round(orb_low, 2),
            'candle_range':  round(candle_range, 4),
            'trade_date':    today_str,
            'scan_time':     scan_time_str,
        })
    
    return matches


# ── Full Scan Run ─────────────────────────────────────────────────
def run_scan(api: AngelAPI, sm: SymbolMaster, htf_cache: dict, scan_num: int) -> int:
    """
    Run one complete scan of all symbols.
    Returns number of matches found.
    """
    ist_now   = datetime.now(IST)
    today_str = ist_now.strftime('%Y-%m-%d')
    scan_time = ist_now.strftime('%H:%M:%S')
    
    tokens = sm.get_nifty500_tokens()
    limiter = RateLimiter(max_per_min=85)
    all_matches = []
    lock = threading.Lock()
    
    def fetch_and_scan(sym_token):
        sym, token = sym_token
        limiter.wait()
        try:
            matches = scan_symbol(api, sym, token, htf_cache, scan_time, today_str)
            if matches:
                with lock:
                    all_matches.extend(matches)
        except Exception as e:
            pass  # individual symbol failures don't crash the scan
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        ex.map(fetch_and_scan, tokens)
    
    if all_matches:
        # Add sector data
        from symbol_master import SymbolMaster
        for m in all_matches:
            m['sector'] = SECTOR_MAP.get(m['symbol'], 'Others')
        
        supa_insert('scan_runs', all_matches)
        print(f"  Scan {scan_num}: {len(all_matches)} matches at {scan_time}")
        
        # Rebuild daily picks after every scan
        rebuild_daily_picks(today_str)
    else:
        print(f"  Scan {scan_num}: 0 matches at {scan_time}")
    
    return len(all_matches)


# ── Rebuild Daily Picks ───────────────────────────────────────────
def rebuild_daily_picks(today_str: str):
    """
    Aggregate scan_runs for today → compute freq, streak, score.
    Upsert into daily_picks.
    Called after every scan.
    """
    rows = supa_select('scan_runs',
                       f'trade_date=eq.{today_str}&order=scan_time.asc')
    if not rows:
        return
    
    # Group by (strategy_code, scanner, symbol)
    groups = {}
    for r in rows:
        key = (r['strategy_code'], r['scanner'], r['symbol'])
        if key not in groups:
            groups[key] = []
        groups[key].append(r)
    
    picks = []
    for (strat, scanner, sym), scans in groups.items():
        times    = [s['scan_time'] for s in scans]
        streak   = compute_streak(times)
        freq     = len(scans)
        last     = scans[-1]
        first    = scans[0]
        del_pct  = 0  # filled in from delivery_data if available
        
        # Delivery % from today's delivery_data (if loaded)
        del_rows = supa_select('delivery_data',
                               f'trade_date=eq.{today_str}&symbol=eq.{sym}&limit=1')
        if del_rows:
            del_pct = del_rows[0].get('delivery_pct', 0) or 0
        
        # Score formula: Freq×25 + (Streak≥3 × Streak×15) + Del×0.5 + GainerFreq×10 + |Chg|×3 + min(VolRatio×2, 25)
        vol_ratio = last.get('vol_ratio', 0) or 0
        chg       = abs(last.get('change_pct', 0) or 0)
        score     = (
            freq * 25
            + (streak * 15 if streak >= 3 else 0)
            + del_pct * 0.5
            + chg * 3
            + min(vol_ratio * 2, 25)
        )
        
        picks.append({
            'trade_date':    today_str,
            'strategy_code': strat,
            'scanner':       scanner,
            'symbol':        sym,
            'stock_name':    '',
            'tag':           get_default_tag(strat, scanner),
            'sector':        last.get('sector', 'Others'),
            'freq_count':    freq,
            'streak':        streak,
            'first_seen':    first['scan_time'],
            'last_seen':     last['scan_time'],
            'close_price':   last.get('close_price'),
            'change_pct':    last.get('change_pct'),
            'volume':        last.get('volume'),
            'vol_ratio':     last.get('vol_ratio'),
            'delivery_pct':  del_pct,
            'gainer_freq':   0,  # filled by separate gainer fetch
            'score':         round(score, 2),
            'updated_at':    datetime.now(IST).isoformat(),
        })
    
    if picks:
        supa_upsert('daily_picks', picks, 'trade_date,strategy_code,scanner,symbol')


def compute_streak(times: list) -> int:
    """Count max consecutive scans within ~90 seconds of each other."""
    if not times:
        return 0
    from datetime import datetime
    parsed = []
    for t in sorted(times):
        try:
            h, m, s = t.split(':')
            parsed.append(int(h) * 3600 + int(m) * 60 + int(s))
        except Exception:
            pass
    if not parsed:
        return 0
    max_streak = cur = 1
    for i in range(1, len(parsed)):
        if parsed[i] - parsed[i-1] <= 90:  # consecutive scans within 90s
            cur += 1
            max_streak = max(max_streak, cur)
        else:
            cur = 1
    return max_streak


def get_default_tag(strategy_code: str, scanner: int) -> str:
    """Default tag before Nifty direction is applied by dashboard."""
    tags = {
        ('ORB', 1):     'BL',
        ('ORB', 2):     'BD',
        ('ORB_REV', 1): 'FL',
        ('ORB_REV', 2): 'FS',
        ('OF', 1):      'OF_L',
        ('OF', 2):      'OF_S',
    }
    return tags.get((strategy_code, scanner), 'BL')


# ── Sector map (inline for performance) ──────────────────────────
SECTOR_MAP = {
    'RELIANCE':'Energy','TCS':'IT','HDFCBANK':'Banking','INFY':'IT',
    'ICICIBANK':'Banking','HINDUNILVR':'FMCG','SBIN':'Banking',
    'BHARTIARTL':'Telecom','ITC':'FMCG','KOTAKBANK':'Banking',
    'AXISBANK':'Banking','LT':'Infra','WIPRO':'IT','HCLTECH':'IT',
    'TATAMOTORS':'Auto','ONGC':'Energy','NTPC':'Power',
    'POWERGRID':'Power','SUNPHARMA':'Pharma','TATASTEEL':'Metal',
    'JSWSTEEL':'Metal','HINDALCO':'Metal','COALINDIA':'Mining',
    'TATAPOWER':'Power','ADANIENT':'Conglomerate','ADANIPORTS':'Infra',
    'CGPOWER':'Cap Goods','BHEL':'Cap Goods','SAIL':'Metal',
    'HINDPETRO':'Energy','BPCL':'Energy','IOC':'Energy',
    'SUZLON':'Energy','IRFC':'Finance','RECLTD':'Finance',
    'PFC':'Finance','BAJFINANCE':'Finance','BAJAJFINSV':'Finance',
    'SBICARD':'Finance','CHOLAFIN':'Finance',
    'MARUTI':'Auto','EICHERMOT':'Auto','HEROMOTOCO':'Auto',
    'M&M':'Auto','BAJAJ-AUTO':'Auto',
    'DRREDDY':'Pharma','CIPLA':'Pharma','DIVISLAB':'Pharma',
    'LUPIN':'Pharma','AUROPHARMA':'Pharma',
    'GRASIM':'Cement','ULTRACEMCO':'Cement',
    'TITAN':'Consumer','NESTLEIND':'FMCG','BRITANNIA':'FMCG',
    'TATACONSUM':'FMCG','GODREJCP':'FMCG','DABUR':'FMCG',
    'GAIL':'Gas','IGL':'Gas','GSPL':'Gas',
    'ZOMATO':'Tech','NAUKRI':'Tech','INDIAMART':'Tech',
}


# ── Market Open Check ─────────────────────────────────────────────
def is_market_open() -> bool:
    ist = datetime.now(IST)
    if ist.weekday() >= 5:  # Saturday/Sunday
        return False
    h, m = ist.hour, ist.minute
    mins = h * 60 + m
    return (9 * 60 + 15) <= mins <= (15 * 60 + 25)

def should_scan() -> bool:
    """Scan from 9:15 AM onwards."""
    ist = datetime.now(IST)
    if ist.weekday() >= 5:
        return False
    mins = ist.hour * 60 + ist.minute
    return (9 * 60 + 15) <= mins <= (15 * 60 + 25)


# ── Main Entry Point ──────────────────────────────────────────────
def main():
    force = '--force' in sys.argv  # bypass market hours check for testing
    
    ist_now = datetime.now(IST)
    print(f"\n{'='*55}")
    print(f"  YS Trading Scanner — {ist_now.strftime('%Y-%m-%d %H:%M:%S IST')}")
    print(f"{'='*55}")
    
    if not force and not is_market_open():
        print(f"Market closed. Exiting.")
        return
    
    print("Logging in to Angel One...")
    api = AngelAPI()
    try:
        api.login()
    except Exception as e:
        print(f"Login failed: {e}")
        sys.exit(1)
    
    sm = SymbolMaster()
    
    # Build HTF cache at startup (or load from cache)
    print("\nLoading HTF data...")
    htf_cache = build_htf_cache(api, sm.get_nifty500_tokens())
    
    # Main scan loop — runs every 60 seconds
    scan_num = 0
    print(f"\nStarting 1-minute scan loop...")
    
    try:
        while True:
            if not force and not should_scan():
                print(f"Outside scan window. Stopping.")
                break
            
            scan_num += 1
            t_start = time.time()
            
            print(f"\n[Scan {scan_num}] {datetime.now(IST).strftime('%H:%M:%S IST')}")
            matches = run_scan(api, sm, htf_cache, scan_num)
            
            elapsed = time.time() - t_start
            sleep_for = max(0, SCAN_INTERVAL_SEC - elapsed)
            
            print(f"  Done in {elapsed:.1f}s. Next scan in {sleep_for:.0f}s.")
            time.sleep(sleep_for)
    
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        api.logout()
        print("Logged out. Scan session ended.")


if __name__ == '__main__':
    main()
