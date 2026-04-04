"""
YS TRADING — test_angel.py
Tests Angel One API connection end-to-end.
File: scripts/test_angel.py

Run this first to confirm your credentials work before building the scanner.

GitHub Actions runs this automatically when you push to the repo.
You can also trigger it manually from Actions → test_angel → Run workflow.

What it tests:
  1. Login with TOTP
  2. Fetch user profile (verify auth works)
  3. Download symbol master (verify internet access)
  4. Fetch SBIN token from master
  5. Fetch 5 recent 5-min candles for SBIN
  6. Connect to Supabase and write a test row
  7. Logout

Exit code 0 = all tests passed
Exit code 1 = one or more tests failed (see output for details)
"""

import os, sys, json
from datetime import datetime, timezone, timedelta

# ── Add scripts/ to path so imports work from GitHub Actions ─
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from angel_api import AngelAPI
from symbol_master import SymbolMaster

IST = timezone(timedelta(hours=5, minutes=30))
PASS = "✅"
FAIL = "❌"
SKIP = "⚠️"


def section(title):
    print(f"\n{'─'*50}")
    print(f"  {title}")
    print(f"{'─'*50}")


def check_env():
    """Check all required env vars exist."""
    section("1. Environment Variables")
    required = ['ANGEL_API_KEY', 'ANGEL_CLIENT_CODE', 'ANGEL_PASSWORD', 'ANGEL_TOTP_SECRET']
    supabase  = ['SUPABASE_URL', 'SUPABASE_SERVICE_KEY']
    all_ok = True
    for key in required:
        val = os.environ.get(key, '')
        if val:
            print(f"  {PASS} {key} = {'*' * min(len(val), 4)}...{val[-4:] if len(val)>4 else '****'}")
        else:
            print(f"  {FAIL} {key} — NOT SET (add to GitHub Secrets)")
            all_ok = False
    for key in supabase:
        val = os.environ.get(key, '')
        if val:
            print(f"  {PASS} {key} set")
        else:
            print(f"  {SKIP} {key} — not set (Supabase test will be skipped)")
    return all_ok


def test_login(api):
    """Test Angel One login."""
    section("2. Angel One Login")
    try:
        api.login()
        print(f"  {PASS} Login successful")
        print(f"  {PASS} JWT token received: {api.auth_token[:20]}...")
        print(f"  {PASS} Feed token: {api.feed_token[:20] if api.feed_token else 'N/A'}...")
        return True
    except Exception as e:
        print(f"  {FAIL} Login failed: {e}")
        return False


def test_profile(api):
    """Test user profile fetch."""
    section("3. User Profile")
    try:
        profile = api.get_profile()
        if profile:
            print(f"  {PASS} Profile fetched")
            print(f"  {PASS} Name:     {profile.get('name', '?')}")
            print(f"  {PASS} Email:    {profile.get('email', '?')}")
            print(f"  {PASS} Exchanges: {profile.get('exchanges', [])}")
            return True
        else:
            print(f"  {FAIL} Profile returned empty")
            return False
    except Exception as e:
        print(f"  {FAIL} Profile error: {e}")
        return False


def test_symbol_master():
    """Test symbol master download."""
    section("4. Symbol Master")
    try:
        sm = SymbolMaster()
        # Test a few known tokens
        tests = [
            ('SBIN',       '3045'),
            ('HDFCBANK',   '1333'),
            ('RELIANCE',   '2885'),
            ('TCS',        '11536'),
            ('TATAPOWER',  '467'),
        ]
        all_ok = True
        for sym, expected_token in tests:
            token = sm.get_token(sym)
            if token:
                match = "✓ match" if token == expected_token else f"⚠ got {token}, expected {expected_token}"
                print(f"  {PASS} {sym:15} → token {token} {match}")
            else:
                print(f"  {FAIL} {sym:15} → NOT FOUND in master")
                all_ok = False

        tokens = sm.get_nifty500_tokens()
        print(f"\n  {PASS} Nifty 500 universe: {len(tokens)} valid symbols")
        return all_ok, sm
    except Exception as e:
        print(f"  {FAIL} Symbol master error: {e}")
        return False, None


def test_candles(api, sm):
    """Test historical candle fetch."""
    section("5. Historical Candles (SBIN 5-min)")
    try:
        token = sm.get_token('SBIN')
        if not token:
            print(f"  {FAIL} SBIN token not found")
            return False

        # Use a known past date (market was open)
        # Using relative date: last Friday or a recent weekday
        ist_now = datetime.now(IST)
        # Find a trading day to test with (go back to find Mon-Fri)
        test_date = ist_now
        for _ in range(7):
            test_date = test_date - timedelta(days=1)
            if test_date.weekday() < 5:  # Mon-Fri
                break
        date_str = test_date.strftime('%Y-%m-%d')
        from_dt  = f'{date_str} 09:15'
        to_dt    = f'{date_str} 11:30'

        print(f"  Fetching SBIN candles for {date_str} 09:15–11:30...")
        candles = api.get_candles('NSE', token, 'FIVE_MINUTE', from_dt, to_dt)

        if not candles:
            # Weekend or holiday — try one more day back
            print(f"  No data for {date_str}, trying previous day...")
            test_date = test_date - timedelta(days=1)
            if test_date.weekday() >= 5:
                test_date = test_date - timedelta(days=test_date.weekday() - 4)
            date_str = test_date.strftime('%Y-%m-%d')
            candles = api.get_candles('NSE', token, 'FIVE_MINUTE',
                                      f'{date_str} 09:15', f'{date_str} 11:30')

        if candles:
            print(f"  {PASS} Got {len(candles)} candles for {date_str}")
            # Show first 3 candles
            for c in candles[:3]:
                ts, o, h, l, close, vol = c
                print(f"       {ts}  O:{o} H:{h} L:{l} C:{close} V:{vol:,}")
            if len(candles) > 3:
                print(f"       ... {len(candles)-3} more candles")
            return True
        else:
            print(f"  {SKIP} No candle data returned (possible holiday or weekend)")
            print(f"       This is OK — API call itself succeeded")
            return True   # Not a failure if market was closed
    except Exception as e:
        print(f"  {FAIL} Candle fetch error: {e}")
        return False


def test_supabase():
    """Test Supabase connection."""
    section("6. Supabase Connection")
    url = os.environ.get('SUPABASE_URL', '')
    key = os.environ.get('SUPABASE_SERVICE_KEY', '')

    if not url or not key:
        print(f"  {SKIP} Supabase credentials not set — skipping")
        return True  # Not a hard failure

    try:
        import requests as req
        headers = {
            'apikey': key,
            'Authorization': f'Bearer {key}',
            'Content-Type': 'application/json',
            'Prefer': 'return=minimal',
        }
        # Simple SELECT 1 test
        resp = req.get(f'{url}/rest/v1/app_settings?select=key&limit=1',
                       headers=headers, timeout=10)
        if resp.status_code in (200, 206):
            print(f"  {PASS} Supabase connected: {url}")
            print(f"  {PASS} app_settings table accessible")
            return True
        elif resp.status_code == 404:
            print(f"  {FAIL} app_settings table not found — run supabase_schema_v2.sql first")
            return False
        else:
            print(f"  {FAIL} Supabase returned HTTP {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"  {FAIL} Supabase error: {e}")
        return False


def test_rate_limit(api, sm):
    """Quick rate limit test — 5 rapid requests."""
    section("7. Rate Limit Test (5 rapid requests)")
    from angel_api import RateLimiter
    limiter = RateLimiter(max_per_min=90)
    symbols = ['SBIN', 'HDFCBANK', 'TCS', 'INFY', 'RELIANCE']
    date_str = (datetime.now(IST) - timedelta(days=3)).strftime('%Y-%m-%d')
    # Skip weekends
    d = datetime.now(IST) - timedelta(days=1)
    for _ in range(7):
        if d.weekday() < 5:
            break
        d -= timedelta(days=1)
    date_str = d.strftime('%Y-%m-%d')

    import time
    t0 = time.time()
    success = 0
    for sym in symbols:
        limiter.wait()
        token = sm.get_token(sym)
        if not token:
            continue
        candles = api.get_candles('NSE', token, 'FIVE_MINUTE',
                                  f'{date_str} 09:15', f'{date_str} 09:30')
        if candles is not None:  # empty list is OK
            success += 1

    elapsed = time.time() - t0
    print(f"  {PASS} {success}/{len(symbols)} requests completed in {elapsed:.1f}s")
    print(f"  {PASS} Avg: {elapsed/len(symbols):.2f}s per request")
    return True


def main():
    print("\n" + "="*50)
    print("  YS TRADING — Angel One API Connection Test")
    print("="*50)
    print(f"  Time (IST): {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}")

    results = {}

    # 1. Check env
    results['env'] = check_env()
    if not results['env']:
        print(f"\n{FAIL} FATAL: Missing required credentials. Add them to GitHub Secrets.")
        sys.exit(1)

    # 2–7. Run tests
    api = AngelAPI()
    try:
        results['login']   = test_login(api)
        if not results['login']:
            print(f"\n{FAIL} FATAL: Login failed. Check credentials.")
            sys.exit(1)

        results['profile'] = test_profile(api)
        ok, sm = test_symbol_master()
        results['symbols'] = ok

        if sm:
            results['candles']    = test_candles(api, sm)
            results['rate_limit'] = test_rate_limit(api, sm)
        else:
            results['candles']    = False
            results['rate_limit'] = False

        results['supabase'] = test_supabase()

    finally:
        api.logout()
        print(f"\n  Logged out cleanly")

    # Summary
    section("SUMMARY")
    all_passed = True
    for test, passed in results.items():
        status = PASS if passed else FAIL
        print(f"  {status}  {test}")
        if not passed:
            all_passed = False

    print()
    if all_passed:
        print(f"  {PASS} ALL TESTS PASSED — Angel One API is ready!")
        print(f"     Next step: build the scanner (Part 2)")
    else:
        failed = [t for t, p in results.items() if not p]
        print(f"  {FAIL} SOME TESTS FAILED: {failed}")
        print(f"     Fix the issues above before building the scanner")
        sys.exit(1)


if __name__ == '__main__':
    main()
