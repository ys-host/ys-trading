"""
YS TRADING — scanner_core.py
Core scanning engine with Parallel Execution + Presence Gate + Stop-Today.
"""

import os, sys, time, json, threading, concurrent.futures, socket
import requests as req
from datetime import datetime, timezone, timedelta
from pathlib import Path

# --- PERFORMANCE CONFIG ---
socket.setdefaulttimeout(45) 
IST            = timezone(timedelta(hours=5, minutes=30))
SCAN_INTERVAL  = 60      
MAX_WORKERS    = 5       # Safe parallel limit
api_lock       = threading.Lock() # Prevents thread collision
HTF_CACHE_FILE = Path('/tmp/ys_htf_cache.json')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from angel_api import AngelAPI
from symbol_master import SymbolMaster

SUPA_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SUPA_KEY = os.environ.get('SUPABASE_SERVICE_KEY', '')

# ── Supabase helpers ──────────────────────────────────────────────
def _headers():
    return {'apikey': SUPA_KEY, 'Authorization': f'Bearer {SUPA_KEY}', 'Content-Type': 'application/json'}

def supa_insert(table, rows):
    if not rows or not SUPA_KEY: return
    try:
        req.post(f'{SUPA_URL}/rest/v1/{table}', json=rows, headers={**_headers(), 'Prefer': 'return=minimal'}, timeout=15)
    except Exception as e: print(f" [DB] Insert failed: {e}", flush=True)

def supa_upsert(table, rows, on_conflict):
    if not rows or not SUPA_KEY: return
    try:
        req.post(f'{SUPA_URL}/rest/v1/{table}?on_conflict={on_conflict}', json=rows, 
                 headers={**_headers(), 'Prefer': 'resolution=merge-duplicates,return=minimal'}, timeout=15)
    except Exception as e: print(f" [DB] Upsert failed: {e}", flush=True)

def supa_select(table, params=''):
    if not SUPA_KEY: return []
    try:
        r = req.get(f'{SUPA_URL}/rest/v1/{table}?{params}', headers=_headers(), timeout=15)
        return r.json() if r.status_code == 200 else []
    except: return []

def supa_patch(table, match_param, data):
    if not SUPA_KEY: return
    try:
        req.patch(f'{SUPA_URL}/rest/v1/{table}?{match_param}', json=data, headers={**_headers(), 'Prefer': 'return=minimal'}, timeout=15)
    except Exception as e: print(f" [DB] Patch failed: {e}", flush=True)

# ── Scanner Controls ───────────────────────────────────────────────
def get_control(today: str) -> dict:
    rows = supa_select('scanner_control', f'control_date=eq.{today}&limit=1')
    if rows: return rows[0]
    init = {'control_date': today, 'is_enabled': True, 'stop_today': False, 'is_user_present': False, 'scan_count': 0}
    supa_upsert('scanner_control', [init], 'control_date')
    return init

def update_control(today: str, data: dict):
    data['updated_at'] = datetime.now(IST).isoformat()
    supa_patch('scanner_control', f'control_date=eq.{today}', data)

def increment_scan_count(today: str):
    rows = supa_select('scanner_control', f'control_date=eq.{today}&select=scan_count')
    count = (rows[0].get('scan_count', 0) if rows else 0) + 1
    update_control(today, {'last_scan_at': datetime.now(IST).isoformat(), 'scan_count': count})

# ── Technical Indicators ──────────────────────────────────────────
def _ema(values, period):
    if len(values) < period: return None
    k = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]: ema = v * k + ema * (1 - k)
    return ema

def _sma(values, period):
    return sum(values[-period:]) / period if len(values) >= period else None

def _rsi(values, period=14):
    if len(values) < period + 1: return None
    gains = [max(values[i] - values[i-1], 0) for i in range(1, len(values))]
    losses = [max(values[i-1] - values[i], 0) for i in range(1, len(values))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    return round(100 - 100 / (1 + ag / al), 1) if al > 0 else 100.0

def _vol_sma(volumes, period=20):
    return sum(volumes[-period:]) / period if len(volumes) >= period else None

# ── Sector Map ────────────────────────────────────────────────────
SECTOR_MAP = {
    'RELIANCE':'Energy','TCS':'IT','HDFCBANK':'Banking','INFY':'IT',
    'ICICIBANK':'Banking','SBIN':'Banking','BHARTIARTL':'Telecom',
    'ITC':'FMCG','KOTAKBANK':'Banking','AXISBANK':'Banking','LT':'Infra',
    'WIPRO':'IT','HCLTECH':'IT','TATAMOTORS':'Auto','ONGC':'Energy',
    'NTPC':'Power','POWERGRID':'Power','SUNPHARMA':'Pharma','TATASTEEL':'Metal',
    'JSWSTEEL':'Metal','HINDALCO':'Metal','COALINDIA':'Mining',
    'TATAPOWER':'Power','ADANIENT':'Conglomerate','ADANIPORTS':'Infra',
    'CGPOWER':'Cap Goods','BHEL':'Cap Goods','SAIL':'Metal',
    'HINDPETRO':'Energy','BPCL':'Energy','IOC':'Energy','SUZLON':'Energy',
    'IRFC':'Finance','RECLTD':'Finance','PFC':'Finance','BAJFINANCE':'Finance',
    'MARUTI':'Auto','EICHERMOT':'Auto','HEROMOTOCO':'Auto','M&M':'Auto',
    'DRREDDY':'Pharma','CIPLA':'Pharma','DIVISLAB':'Pharma','LUPIN':'Pharma',
    'GRASIM':'Cement','ULTRACEMCO':'Cement','TITAN':'Consumer',
    'NESTLEIND':'FMCG','BRITANNIA':'FMCG','GAIL':'Gas','IGL':'Gas',
    'ZOMATO':'Tech','HINDUNILVR':'FMCG','BAJAJFINSV':'Finance',
    'SBICARD':'Finance','CHOLAFIN':'Finance','AUROPHARMA':'Pharma',
}

# ── Single Symbol Scan Engine ─────────────────────────────────────
def analyze_single_stock(args):
    """Parallel Worker for Strategy Logic."""
    api, sym, token, htf_cache, scan_time_str, today_str = args
    ist_now = datetime.now(IST)
    from_dt, to_dt = f'{today_str} 09:15', ist_now.strftime('%Y-%m-%d %H:%M')

    try:
        with api_lock:
            candles = api.get_candles('NSE', token, 'ONE_MINUTE', from_dt, to_dt)
        
        if not candles or len(candles) < 5: return []

        highs, lows, closes, opens, volumes = ([float(c[i]) for c in candles] for i in [2, 3, 4, 1, 5])
        
        orb_high, orb_low = highs[0], lows[0]
        cur_open, cur_high, cur_low, cur_close, cur_vol = opens[-1], highs[-1], lows[-1], closes[-1], volumes[-1]
        prev_high, prev_low, prev_close = (highs[-2] if len(highs) >= 2 else highs[-1]), (lows[-2] if len(lows) >= 2 else lows[-1]), opens[0]

        vol_sma = _vol_sma(volumes[:-1], 20) or cur_vol
        vol_ratio = cur_vol / vol_sma if vol_sma > 0 else 0
        c_range = (cur_high - cur_low) / cur_close if cur_close > 0 else 0
        gap_pct = (cur_open - prev_close) / prev_close if prev_close > 0 else 0
        chg_pct = (cur_close - prev_close) / prev_close * 100 if prev_close > 0 else 0

        htf = htf_cache.get(sym, {})
        above_ema20, above_sma50, trend_up = htf.get('above_ema20'), htf.get('above_sma50'), htf.get('trend_up')

        base = {
            'symbol': sym, 'token': token, 'sector': SECTOR_MAP.get(sym, 'Others'),
            'close_price': round(cur_close, 2), 'change_pct': round(chg_pct, 4),
            'volume': cur_vol, 'vol_sma20': int(vol_sma), 'vol_ratio': round(vol_ratio, 2),
            'orb_high': round(orb_high, 2), 'orb_low': round(orb_low, 2),
            'candle_range': round(c_range, 4), 'trade_date': today_str, 'scan_time': scan_time_str,
        }

        matches = []
        # Bullish ORB
        if (abs(gap_pct) <= 0.015 and cur_close > cur_open and cur_close > prev_high and 2.5 <= vol_ratio <= 8.0 
            and c_range <= 0.025 and cur_close < prev_close * 1.05 and cur_close >= 150 and (above_ema20 is None or above_ema20)):
            matches.append({**base, 'strategy_code': 'ORB', 'scanner': 1, 'tag': 'BL'})
        # Bearish ORB
        if (gap_pct <= 0.005 and gap_pct >= -0.030 and cur_close < cur_open and cur_close < prev_low and 2.5 <= vol_ratio <= 8.0 
            and c_range <= 0.025 and cur_close > prev_close * 0.94 and cur_close >= 150 and (above_ema20 is None or not above_ema20)):
            matches.append({**base, 'strategy_code': 'ORB', 'scanner': 2, 'tag': 'BD'})
        return matches
    except: return []

# ── Master Scan Run ───────────────────────────────────────────────
def run_scan(api, sm, htf_cache, scan_num, today_str, scan_time):
    tokens = sm.get_nifty500_tokens()
    all_matches = []
    
    # MENTOR NOTE: We pack the arguments and send them to the ThreadPool.
    task_args = [(api, sym, tok, htf_cache, scan_time, today_str) for sym, tok in tokens]

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        results = list(ex.map(analyze_single_stock, task_args))

    for m_list in results:
        if m_list: all_matches.extend(m_list)

    if all_matches:
        supa_insert('scan_runs', all_matches)
        rebuild_daily_picks(today_str)

    print(f"  Scan {scan_num} ({scan_time}): {len(all_matches)} matches", flush=True)

# ── Daily Picks & Streaks ─────────────────────────────────────────
def compute_streak(times):
    if not times: return 0
    parsed = sorted([int(t[:2])*3600 + int(t[3:5])*60 + int(t[6:8]) for t in times if len(t) >= 8])
    max_s = cur = 1
    for i in range(1, len(parsed)):
        if parsed[i] - parsed[i-1] <= 90:
            cur += 1
            max_s = max(max_s, cur)
        else: cur = 1
    return max_s

def rebuild_daily_picks(today_str):
    rows = supa_select('scan_runs', f'trade_date=eq.{today_str}&order=scan_time.asc')
    if not rows: return
    groups = {}
    for r in rows: groups.setdefault((r['strategy_code'], r['scanner'], r['symbol']), []).append(r)
    picks = []
    for (strat, scanner, sym), scans in groups.items():
        streak = compute_streak([s['scan_time'] for s in scans])
        last, first = scans[-1], scans[0]
        score = len(scans)*25 + (streak*15 if streak >= 3 else 0) + abs(last.get('change_pct',0))*3
        picks.append({
            'trade_date': today_str, 'strategy_code': strat, 'scanner': scanner, 'symbol': sym,
            'tag': last.get('tag', 'BL'), 'sector': last.get('sector', 'Others'), 'freq_count': len(scans),
            'streak': streak, 'first_seen': first.get('scan_time'), 'last_seen': last.get('scan_time'),
            'close_price': last.get('close_price'), 'vol_ratio': last.get('vol_ratio'), 'score': round(score, 2)
        })
    if picks: supa_upsert('daily_picks', picks, 'trade_date,strategy_code,scanner,symbol')

# ── HTF Cache Builder ─────────────────────────────────────────────
def build_htf_cache(api, sm):
    today = datetime.now(IST).strftime('%Y-%m-%d')
    if HTF_CACHE_FILE.exists():
        try:
            cache = json.loads(HTF_CACHE_FILE.read_text())
            if cache.get('_date') == today: return cache
        except: pass
    
    print(f"  Building HTF cache...", flush=True)
    from_dt, to_dt = (datetime.now(IST) - timedelta(days=90)).strftime('%Y-%m-%d 09:00'), datetime.now(IST).strftime('%Y-%m-%d 15:30')
    cache = {'_date': today}
    db_rows = []

    def fetch_htf(sym_token):
        sym, token = sym_token
        with api_lock:
            candles = api.get_candles('NSE', token, 'ONE_DAY', from_dt, to_dt)
        if not candles or len(candles) < 21: return None
        closes = [float(c[4]) for c in candles]
        ema20, sma50, rsi14, last = _ema(closes, 20), _sma(closes, 50), _rsi(closes, 14), closes[-1]
        return sym, {'ema20': round(ema20,2), 'sma50': round(sma50,2), 'rsi14': rsi14, 'last_close': last, 
                     'above_ema20': last > ema20, 'above_sma50': last > sma50, 'trend_up': last > closes[-21]}

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for res in ex.map(fetch_htf, sm.get_nifty500_tokens()):
            if res:
                s, d = res
                cache[s] = d
                db_rows.append({'cache_date': today, 'symbol': s, 'daily_ema20': d['ema20'], 'daily_sma50': d['sma50'], 'daily_trend': 'UP' if d['trend_up'] else 'DOWN'})

    HTF_CACHE_FILE.write_text(json.dumps(cache))
    for i in range(0, len(db_rows), 200): supa_upsert('mtf_cache', db_rows[i:i+200], 'cache_date,symbol')
    return cache

# ── Main Loop ─────────────────────────────────────────────────────
def main():
    api = AngelAPI()
    api.login()
    sm = SymbolMaster()
    htf_cache = build_htf_cache(api, sm)
    today_str = datetime.now(IST).strftime('%Y-%m-%d')
    warned_11_15 = False
    scan_num = 0

    try:
        while True:
            now = datetime.now(IST)
            mins = now.hour * 60 + now.minute
            today_str = now.strftime('%Y-%m-%d')
            scan_time = now.strftime('%H:%M:%S')

            # Presence Gate Rules
            control = get_control(today_str)
            if control.get('stop_today'): break
            if mins >= (11 * 60 + 30) and not control.get('is_user_present'):
                update_control(today_str, {'stop_today': True, 'stop_reason': 'auto_absence'})
                break
            
            # Warn at 11:15
            if not warned_11_15 and 11*60+15 <= mins < 11*60+30 and not control.get('is_user_present'):
                update_control(today_str, {'shutdown_warned': True})
                warned_11_15 = True

            # End of Day
            if mins >= 15 * 60 + 30:
                update_control(today_str, {'is_enabled': False, 'stop_today': True})
                break

            # Scan Window
            if (9 * 60 + 15 <= mins <= 15 * 60 + 25):
                scan_num += 1
                t0 = time.time()
                run_scan(api, sm, htf_cache, scan_num, today_str, scan_time)
                increment_scan_count(today_str)
                time.sleep(max(0, SCAN_INTERVAL - (time.time() - t0)))
            else:
                time.sleep(30)

    finally:
        api.logout()

if __name__ == '__main__':
    main()
