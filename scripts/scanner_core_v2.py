"""
YS TRADING — scanner_core_v2.py
Core scanning engine with Presence Gate + Stop-Today controls.
File: scripts/scanner_core.py  (replace the v1 file)

PRESENCE LOGIC (from spec):
  Rule 1: Morning_Session = start → 11:30 | Afternoon_Session = 11:30 → 3:30
  Rule 2: is_user_present = False every morning (fresh from DB)
  Rule 3: Set to True IF "I'm Present" button clicked OR manual scan run before 11:25 AM
  Rule 4: At 11:30 → if not present → auto-shutdown
  Rule 5: At 15:30 → reset all flags, clean exit

STOP-TODAY LOGIC:
  Dashboard writes stop_today=True to scanner_control → Python sees it → exits loop
  "Scan Now" button resets stop_today=False and dispatches new job
"""

import os, sys, time, json, threading, concurrent.futures
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from angel_api import AngelAPI, RateLimiter
from symbol_master import SymbolMaster

IST            = timezone(timedelta(hours=5, minutes=30))
SCAN_INTERVAL  = 60      # seconds between scans
MAX_WORKERS    = 20      # parallel API threads
HTF_CACHE_FILE = Path('/tmp/ys_htf_cache.json')

# Supabase REST
SUPA_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SUPA_KEY = os.environ.get('SUPABASE_SERVICE_KEY', '')

import requests as req

# ── Supabase helpers ──────────────────────────────────────────────
def _headers():
    return {
        'apikey': SUPA_KEY,
        'Authorization': f'Bearer {SUPA_KEY}',
        'Content-Type': 'application/json',
    }

def supa_insert(table, rows):
    if not rows or not SUPA_KEY:
        return
    try:
        r = req.post(f'{SUPA_URL}/rest/v1/{table}',
                     json=rows,
                     headers={**_headers(), 'Prefer': 'return=minimal'},
                     timeout=15)
        if r.status_code not in (200, 201):
            print(f"  [DB] Insert error {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"  [DB] Insert failed: {e}")

def supa_upsert(table, rows, on_conflict):
    if not rows or not SUPA_KEY:
        return
    try:
        r = req.post(
            f'{SUPA_URL}/rest/v1/{table}?on_conflict={on_conflict}',
            json=rows,
            headers={**_headers(), 'Prefer': 'resolution=merge-duplicates,return=minimal'},
            timeout=15)
        if r.status_code not in (200, 201):
            print(f"  [DB] Upsert error {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"  [DB] Upsert failed: {e}")

def supa_select(table, params=''):
    if not SUPA_KEY:
        return []
    try:
        r = req.get(f'{SUPA_URL}/rest/v1/{table}?{params}',
                    headers=_headers(), timeout=15)
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []

def supa_patch(table, match_param, data):
    """Update a row."""
    if not SUPA_KEY:
        return
    try:
        r = req.patch(
            f'{SUPA_URL}/rest/v1/{table}?{match_param}',
            json=data,
            headers={**_headers(), 'Prefer': 'return=minimal'},
            timeout=15)
        if r.status_code not in (200, 204):
            print(f"  [DB] Patch error {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"  [DB] Patch failed: {e}")


# ── Scanner Control ───────────────────────────────────────────────
def get_control(today: str) -> dict:
    """Read or create today's scanner_control row."""
    rows = supa_select('scanner_control', f'control_date=eq.{today}&limit=1')
    if rows:
        return rows[0]
    # First time today — initialize
    init = {
        'control_date':    today,
        'is_enabled':      True,
        'stop_today':      False,
        'is_user_present': False,
        'scan_count':      0,
    }
    supa_upsert('scanner_control', [init], 'control_date')
    return init

def update_control(today: str, data: dict):
    """Patch scanner_control for today."""
    data['updated_at'] = datetime.now(IST).isoformat()
    supa_patch('scanner_control', f'control_date=eq.{today}', data)

def increment_scan_count(today: str, scan_time: str):
    """Mark last scan time and increment count."""
    rows = supa_select('scanner_control', f'control_date=eq.{today}&select=scan_count')
    count = (rows[0].get('scan_count', 0) if rows else 0) + 1
    update_control(today, {
        'last_scan_at': datetime.now(IST).isoformat(),
        'scan_count':   count,
    })


# ── Presence Gate ─────────────────────────────────────────────────
def check_presence_gate(control: dict, ist_now: datetime) -> bool:
    """
    Rule 4: At exactly 11:30 AM check.
    If user not present → return False (trigger shutdown).
    Before 11:30 AM → always True.
    After 11:30 AM → only True if user_present was confirmed.
    """
    mins = ist_now.hour * 60 + ist_now.minute

    # Before 11:30 — no gate
    if mins < 11 * 60 + 30:
        return True

    # At and after 11:30 — require confirmed presence
    return control.get('is_user_present', False)


def check_end_of_day(ist_now: datetime) -> bool:
    """Rule 5: End of day at 15:30."""
    return ist_now.hour * 60 + ist_now.minute >= 15 * 60 + 30


# ── Technical Indicators ──────────────────────────────────────────
def _ema(values, period):
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema

def _sma(values, period):
    return sum(values[-period:]) / period if len(values) >= period else None

def _rsi(values, period=14):
    if len(values) < period + 1:
        return None
    gains = [max(values[i] - values[i-1], 0) for i in range(1, len(values))]
    losses = [max(values[i-1] - values[i], 0) for i in range(1, len(values))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    return round(100 - 100 / (1 + ag / al), 1) if al > 0 else 100.0

def _vol_sma(volumes, period=20):
    return sum(volumes[-period:]) / period if len(volumes) >= period else None


# ── HTF Cache ─────────────────────────────────────────────────────
def build_htf_cache(api: AngelAPI, sm) -> dict:
    ist_now = datetime.now(IST)
    today   = ist_now.strftime('%Y-%m-%d')

    if HTF_CACHE_FILE.exists():
        try:
            cache = json.loads(HTF_CACHE_FILE.read_text())
            if cache.get('_date') == today:
                print(f"  HTF cache loaded: {len(cache)-1} symbols")
                return cache
        except Exception:
            pass

    print(f"  Building HTF cache ({len(sm.get_nifty500_tokens())} symbols)...")
    from_dt = (ist_now - timedelta(days=90)).strftime('%Y-%m-%d %09:00')
    to_dt   = ist_now.strftime('%Y-%m-%d %15:30')

    cache    = {'_date': today}
    limiter  = RateLimiter(max_per_min=80)
    db_rows  = []

    def fetch_one(sym_token):
        sym, token = sym_token
        limiter.wait()
        candles = api.get_candles('NSE', token, 'ONE_DAY', from_dt, to_dt)
        if not candles or len(candles) < 21:
            return sym, None
        closes = [float(c[4]) for c in candles]
        ema20  = _ema(closes, 20)
        sma50  = _sma(closes, 50) if len(closes) >= 50 else None
        rsi14  = _rsi(closes, 14)
        last   = closes[-1]
        ago20  = closes[-21] if len(closes) >= 21 else last
        return sym, {
            'ema20':      round(ema20, 2)  if ema20 else None,
            'sma50':      round(sma50, 2)  if sma50 else None,
            'rsi14':      round(rsi14, 1)  if rsi14 else None,
            'last_close': round(last, 2),
            'above_ema20': last > ema20    if ema20 else None,
            'above_sma50': last > sma50    if sma50 else None,
            'trend_up':   last > ago20,
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for sym, data in ex.map(fetch_one, sm.get_nifty500_tokens()):
            if data:
                cache[sym] = data
                db_rows.append({
                    'cache_date':  today,
                    'symbol':      sym,
                    'daily_ema20': data['ema20'],
                    'daily_sma50': data['sma50'],
                    'daily_rsi14': data['rsi14'],
                    'daily_close': data['last_close'],
                    'above_ema20': data['above_ema20'],
                    'above_sma50': data['above_sma50'],
                    'daily_trend': 'UP' if data['trend_up'] else 'DOWN',
                })

    HTF_CACHE_FILE.write_text(json.dumps(cache))
    # Save to Supabase in batches of 200
    for i in range(0, len(db_rows), 200):
        supa_upsert('mtf_cache', db_rows[i:i+200], 'cache_date,symbol')

    print(f"  HTF cache built: {len(cache)-1} symbols")
    return cache


# ── Single Symbol Scan ─────────────────────────────────────────────
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

def scan_symbol(api, sym, token, htf_cache, scan_time_str, today_str):
    ist_now = datetime.now(IST)
    from_dt = f'{today_str} 09:15'
    to_dt   = ist_now.strftime('%Y-%m-%d %H:%M')

    candles = api.get_candles('NSE', token, 'ONE_MINUTE', from_dt, to_dt)
    if not candles or len(candles) < 5:
        return []

    opens   = [float(c[1]) for c in candles]
    highs   = [float(c[2]) for c in candles]
    lows    = [float(c[3]) for c in candles]
    closes  = [float(c[4]) for c in candles]
    volumes = [int(c[5])   for c in candles]

    orb_high  = highs[0]
    orb_low   = lows[0]
    cur_open  = opens[-1]
    cur_high  = highs[-1]
    cur_low   = lows[-1]
    cur_close = closes[-1]
    cur_vol   = volumes[-1]
    prev_high = highs[-2]  if len(highs) >= 2  else highs[-1]
    prev_low  = lows[-2]   if len(lows)  >= 2  else lows[-1]
    prev_close = opens[0]

    vol_sma   = _vol_sma(volumes[:-1], 20) or cur_vol
    vol_ratio = cur_vol / vol_sma if vol_sma > 0 else 0
    c_range   = (cur_high - cur_low) / cur_close if cur_close > 0 else 0
    gap_pct   = (cur_open - prev_close) / prev_close if prev_close > 0 else 0
    chg_pct   = (cur_close - prev_close) / prev_close * 100 if prev_close > 0 else 0

    htf         = htf_cache.get(sym, {})
    above_ema20 = htf.get('above_ema20')
    above_sma50 = htf.get('above_sma50')
    trend_up    = htf.get('trend_up')

    base = {
        'symbol': sym, 'token': token,
        'sector': SECTOR_MAP.get(sym, 'Others'),
        'close_price': round(cur_close, 2),
        'change_pct':  round(chg_pct, 4),
        'volume':      cur_vol,
        'vol_sma20':   int(vol_sma),
        'vol_ratio':   round(vol_ratio, 2),
        'orb_high':    round(orb_high, 2),
        'orb_low':     round(orb_low, 2),
        'candle_range': round(c_range, 4),
        'trade_date':  today_str,
        'scan_time':   scan_time_str,
    }

    matches = []

    # ── Scanner 1: Bullish (BL / FS) ──────────────────────────────
    if (abs(gap_pct) <= 0.015
            and cur_close > cur_open
            and cur_close > prev_high
            and 2.5 <= vol_ratio <= 8.0
            and c_range <= 0.025
            and cur_close < prev_close * 1.05
            and cur_close >= 150
            and (above_ema20 is None or above_ema20)
            and (above_sma50 is None or above_sma50)
            and (trend_up    is None or trend_up)):
        matches.append({**base, 'strategy_code': 'ORB', 'scanner': 1, 'tag': 'BL'})

    # ── Scanner 2: Bearish (BD / FL) ──────────────────────────────
    if (gap_pct <= 0.005 and gap_pct >= -0.030
            and cur_close < cur_open
            and cur_close < prev_low
            and 2.5 <= vol_ratio <= 8.0
            and c_range <= 0.025
            and cur_close > prev_close * 0.94
            and cur_close >= 150
            and (above_ema20 is None or not above_ema20)
            and (above_sma50 is None or not above_sma50)
            and (trend_up    is None or not trend_up)):
        matches.append({**base, 'strategy_code': 'ORB', 'scanner': 2, 'tag': 'BD'})

    return matches


# ── Streak calculation ────────────────────────────────────────────
def compute_streak(times):
    if not times:
        return 0
    parsed = sorted([int(t[:2])*3600 + int(t[3:5])*60 + int(t[6:8]) for t in times if len(t) >= 8])
    max_s = cur = 1
    for i in range(1, len(parsed)):
        if parsed[i] - parsed[i-1] <= 90:
            cur += 1
            max_s = max(max_s, cur)
        else:
            cur = 1
    return max_s


# ── Full Scan Run ─────────────────────────────────────────────────
def run_scan(api, sm, htf_cache, scan_num, today_str, scan_time):
    tokens     = sm.get_nifty500_tokens()
    limiter    = RateLimiter(max_per_min=85)
    all_matches = []
    lock       = threading.Lock()

    def fetch_one(sym_token):
        sym, token = sym_token
        limiter.wait()
        try:
            m = scan_symbol(api, sym, token, htf_cache, scan_time, today_str)
            if m:
                with lock:
                    all_matches.extend(m)
        except Exception:
            pass

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        ex.map(fetch_one, tokens)

    if all_matches:
        supa_insert('scan_runs', all_matches)
        rebuild_daily_picks(today_str)

    print(f"  Scan {scan_num} ({scan_time}): {len(all_matches)} matches")
    return len(all_matches)


# ── Rebuild Daily Picks ───────────────────────────────────────────
def rebuild_daily_picks(today_str):
    rows = supa_select('scan_runs', f'trade_date=eq.{today_str}&order=scan_time.asc')
    if not rows:
        return

    groups = {}
    for r in rows:
        key = (r['strategy_code'], r['scanner'], r['symbol'])
        groups.setdefault(key, []).append(r)

    picks = []
    for (strat, scanner, sym), scans in groups.items():
        times   = [s['scan_time'] for s in scans if s.get('scan_time')]
        streak  = compute_streak(times)
        freq    = len(scans)
        last    = scans[-1]
        first   = scans[0]
        vol_ratio = last.get('vol_ratio', 0) or 0
        chg     = abs(last.get('change_pct', 0) or 0)
        score   = freq*25 + (streak*15 if streak >= 3 else 0) + chg*3 + min(vol_ratio*2, 25)

        picks.append({
            'trade_date':    today_str,
            'strategy_code': strat,
            'scanner':       scanner,
            'symbol':        sym,
            'tag':           last.get('tag', 'BL'),
            'sector':        last.get('sector', 'Others'),
            'freq_count':    freq,
            'streak':        streak,
            'first_seen':    first.get('scan_time'),
            'last_seen':     last.get('scan_time'),
            'close_price':   last.get('close_price'),
            'change_pct':    last.get('change_pct'),
            'volume':        last.get('volume'),
            'vol_ratio':     vol_ratio,
            'score':         round(score, 2),
            'updated_at':    datetime.now(IST).isoformat(),
        })

    if picks:
        supa_upsert('daily_picks', picks, 'trade_date,strategy_code,scanner,symbol')


# ── End of Day Reset ──────────────────────────────────────────────
def end_of_day_reset(today_str):
    """Rule 5: At 15:30 reset all flags, clean exit."""
    print("\n[EOD] 15:30 — End of day reset")
    update_control(today_str, {
        'is_enabled':  False,
        'stop_today':  True,
        'stop_reason': 'end_of_day',
        'stopped_at':  datetime.now(IST).isoformat(),
    })
    print("[EOD] Flags reset. Clean exit.")


# ── Main ──────────────────────────────────────────────────────────
def main():
    force = '--force' in sys.argv

    ist_now   = datetime.now(IST)
    today_str = ist_now.strftime('%Y-%m-%d')
    mins_now  = ist_now.hour * 60 + ist_now.minute

    print(f"\n{'='*55}")
    print(f"  YS Scanner v2 — {ist_now.strftime('%Y-%m-%d %H:%M:%S IST')}")
    print(f"{'='*55}")

    if not force and ist_now.weekday() >= 5:
        print("Weekend — no scan.")
        return

    if not force and mins_now > 15 * 60 + 30:
        print("After market close — no scan.")
        return

    # Login
    print("\n[1] Connecting to Angel One...")
    api = AngelAPI()
    try:
        api.login()
    except Exception as e:
        print(f"Login failed: {e}")
        sys.exit(1)

    sm = SymbolMaster()

    # HTF cache
    print("\n[2] Loading HTF data...")
    htf_cache = build_htf_cache(api, sm)

    # Initialize scanner control for today
    control = get_control(today_str)
    print(f"\n[3] Scanner control: is_enabled={control.get('is_enabled')} | "
          f"stop_today={control.get('stop_today')} | "
          f"is_user_present={control.get('is_user_present')}")

    # Check if already stopped
    if control.get('stop_today'):
        print("Stop-Today flag is set. Exiting without scanning.")
        print("To resume: click 'Scan Now' from dashboard (resets stop flag).")
        api.logout()
        return

    # Scan loop
    scan_num      = 0
    warned_11_15  = False
    print(f"\n[4] Starting 1-min scan loop...\n")

    try:
        while True:
            ist_now   = datetime.now(IST)
            today_str = ist_now.strftime('%Y-%m-%d')
            mins      = ist_now.hour * 60 + ist_now.minute
            scan_time = ist_now.strftime('%H:%M:%S')

            # ── Rule 5: End of day ────────────────────────────────
            if check_end_of_day(ist_now):
                end_of_day_reset(today_str)
                break

            # ── Refresh control flags each loop ──────────────────
            control = get_control(today_str)

            # ── Stop-Today check ──────────────────────────────────
            if control.get('stop_today'):
                print(f"\n[STOP] Stop-Today flag detected at {scan_time}.")
                print("       Scanner stopped. Use 'Scan Now' to resume.")
                break

            # ── Rule 4: Presence gate at 11:30 ───────────────────
            if not force and not check_presence_gate(control, ist_now):
                print(f"\n[AUTO-SHUTDOWN] 11:30 reached. User not confirmed present.")
                print("                Setting stop_today=True.")
                update_control(today_str, {
                    'stop_today':  True,
                    'stop_reason': 'auto_absence',
                    'stopped_at':  datetime.now(IST).isoformat(),
                })
                break

            # ── Countdown warning at 11:15 ────────────────────────
            if not warned_11_15 and 11 * 60 + 15 <= mins < 11 * 60 + 30:
                if not control.get('is_user_present'):
                    print(f"\n[WARNING] 11:15 AM — Auto-shutdown in 15 min if presence not confirmed.")
                    print("          Click 'I\'m Present' in dashboard to keep scanner running.")
                    update_control(today_str, {'shutdown_warned': True})
                    warned_11_15 = True

            # ── Outside scan window ───────────────────────────────
            if not force and not (9 * 60 + 15 <= mins <= 15 * 60 + 25):
                print(f"  Outside scan window ({scan_time}). Waiting...")
                time.sleep(30)
                continue

            # ── Run one scan ─────────────────────────────────────
            scan_num += 1
            t0 = time.time()
            run_scan(api, sm, htf_cache, scan_num, today_str, scan_time)
            increment_scan_count(today_str, scan_time)

            elapsed    = time.time() - t0
            sleep_for  = max(0, SCAN_INTERVAL - elapsed)
            print(f"  Scan done in {elapsed:.1f}s. Next in {sleep_for:.0f}s.")
            time.sleep(sleep_for)

    except KeyboardInterrupt:
        print("\n[STOP] Keyboard interrupt.")
    finally:
        api.logout()
        print(f"[EXIT] Logged out. Total scans: {scan_num}")


if __name__ == '__main__':
    main()
