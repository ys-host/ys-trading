#!/usr/bin/env python3
"""
YS TRADING — SERVER-SIDE FETCH TEST
Run this locally first: python test_fetch.py
This tells us EXACTLY what works before building anything.
"""

import requests
import json
import time
import re
from datetime import datetime

print("=" * 60)
print("YS Trading — Server-Side Data Fetch Test")
print("=" * 60)

# ── BROWSER HEADERS (critical — without these, all requests fail) ──
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-IN,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}

results = {}

# ════════════════════════════════════════════════════════════════
# TEST 1: Chartink Scanner (Session + CSRF approach)
# ════════════════════════════════════════════════════════════════
print("\n── TEST 1: Chartink Scanner ──")
print("Attempting session-based CSRF fetch...")

CHARTINK_SLUG = 'scanner-1-bullish-breakout-fakeout-short'

try:
    session = requests.Session()

    # Step 1: Visit Chartink homepage to establish session cookies
    print("  Step 1: Getting Chartink session...")
    r0 = session.get(
        'https://chartink.com/',
        headers=HEADERS,
        timeout=15
    )
    print(f"  Homepage: HTTP {r0.status_code}, cookies: {list(session.cookies.keys())}")

    # Step 2: Visit the specific screener page to get CSRF token
    print(f"  Step 2: Fetching screener page...")
    r1 = session.get(
        f'https://chartink.com/screener/{CHARTINK_SLUG}',
        headers={**HEADERS, 'Referer': 'https://chartink.com/'},
        timeout=20
    )
    print(f"  Screener page: HTTP {r1.status_code}, length: {len(r1.text)}")

    # Extract CSRF token
    csrf = None
    patterns = [
        r'meta[^>]+csrf-token[^>]+content="([^"]+)"',
        r'csrf-token"\s+content="([^"]+)"',
        r'"_token"\s*:\s*"([^"]+)"',
        r"csrf[_-]token['\"]?\s*[=:]\s*['\"]([^'\"]+)['\"]",
    ]
    for pat in patterns:
        m = re.search(pat, r1.text)
        if m:
            csrf = m.group(1)
            break

    print(f"  CSRF token: {csrf[:20] + '...' if csrf and len(csrf) > 20 else csrf}")

    if csrf:
        # Step 3: POST to get scanner data
        print("  Step 3: Posting to screener/process...")
        r2 = session.post(
            'https://chartink.com/screener/process',
            data={'_token': csrf, 'scan_clause': ''},
            headers={
                **HEADERS,
                'X-Requested-With': 'XMLHttpRequest',
                'Referer': f'https://chartink.com/screener/{CHARTINK_SLUG}',
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json, text/javascript, */*; q=0.01',
            },
            timeout=25
        )
        print(f"  Process endpoint: HTTP {r2.status_code}")

        if r2.status_code == 200:
            try:
                data = r2.json()
                stocks = data.get('data', [])
                print(f"\n  ✅ CHARTINK WORKS! Got {len(stocks)} stocks")
                if stocks:
                    print(f"  Sample stocks: {[s.get('nsecode', s.get('symbol','?')) for s in stocks[:5]]}")
                results['chartink'] = {'status': 'SUCCESS', 'count': len(stocks), 'sample': stocks[:3]}
            except Exception as e:
                print(f"  ⚠ Response not JSON: {r2.text[:200]}")
                results['chartink'] = {'status': 'NON_JSON', 'response': r2.text[:200]}
        elif r2.status_code == 419:
            print("  ❌ CSRF mismatch (419) — Chartink blocking server requests")
            results['chartink'] = {'status': 'CSRF_BLOCKED'}
        elif r2.status_code == 403:
            print("  ❌ Forbidden (403) — Chartink blocking server requests")
            results['chartink'] = {'status': 'FORBIDDEN'}
        else:
            print(f"  ❌ Unexpected status: {r2.status_code}")
            results['chartink'] = {'status': f'HTTP_{r2.status_code}', 'body': r2.text[:200]}
    else:
        print("  ❌ Could not find CSRF token in page")
        results['chartink'] = {'status': 'NO_CSRF', 'page_length': len(r1.text)}

except requests.exceptions.ConnectionError as e:
    print(f"  ❌ Connection refused: {e}")
    results['chartink'] = {'status': 'CONNECTION_REFUSED'}
except Exception as e:
    print(f"  ❌ Error: {e}")
    results['chartink'] = {'status': 'ERROR', 'message': str(e)}

time.sleep(2)

# ════════════════════════════════════════════════════════════════
# TEST 2: NSE Live Gainers API
# ════════════════════════════════════════════════════════════════
print("\n── TEST 2: NSE Live Gainers ──")

try:
    session2 = requests.Session()

    # NSE requires session cookies from homepage visit
    print("  Getting NSE session...")
    r3 = session2.get(
        'https://www.nseindia.com/',
        headers=HEADERS,
        timeout=15
    )
    print(f"  NSE homepage: HTTP {r3.status_code}, cookies: {len(session2.cookies)}")

    time.sleep(1)

    print("  Fetching NSE gainers API...")
    r4 = session2.get(
        'https://www.nseindia.com/api/live-analysis-variations?index=gainers',
        headers={
            **HEADERS,
            'Referer': 'https://www.nseindia.com/market-data/live-equity-market',
            'Accept': 'application/json',
        },
        timeout=20
    )
    print(f"  NSE gainers: HTTP {r4.status_code}")

    if r4.status_code == 200:
        try:
            data = r4.json()
            items = data.get('data', data.get('NIFTY', []))
            print(f"\n  ✅ NSE WORKS! Got {len(items)} gainers")
            if items:
                print(f"  Sample: {[i.get('symbol', '?') for i in items[:5]]}")
            results['nse_gainers'] = {'status': 'SUCCESS', 'count': len(items)}
        except:
            print(f"  ⚠ Not JSON: {r4.text[:200]}")
            results['nse_gainers'] = {'status': 'NON_JSON'}
    else:
        print(f"  ❌ HTTP {r4.status_code}")
        results['nse_gainers'] = {'status': f'HTTP_{r4.status_code}'}

except Exception as e:
    print(f"  ❌ Error: {e}")
    results['nse_gainers'] = {'status': 'ERROR', 'message': str(e)}

time.sleep(2)

# ════════════════════════════════════════════════════════════════
# TEST 3: Yahoo Finance (NSE Gainers alternative — usually works)
# ════════════════════════════════════════════════════════════════
print("\n── TEST 3: Yahoo Finance (NSE data alternative) ──")

try:
    r5 = requests.get(
        'https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI?interval=1d&range=1d',
        headers=HEADERS,
        timeout=15
    )
    print(f"  Yahoo Finance Nifty: HTTP {r5.status_code}")
    if r5.status_code == 200:
        data = r5.json()
        price = data.get('chart', {}).get('result', [{}])[0].get('meta', {}).get('regularMarketPrice')
        print(f"  ✅ Yahoo Finance WORKS! Nifty = {price}")
        results['yahoo'] = {'status': 'SUCCESS', 'nifty': price}
    else:
        results['yahoo'] = {'status': f'HTTP_{r5.status_code}'}
except Exception as e:
    print(f"  ❌ Error: {e}")
    results['yahoo'] = {'status': 'ERROR'}

time.sleep(1)

# ════════════════════════════════════════════════════════════════
# TEST 4: NSE Bhavcopy (Archive — this DOES work server-side)
# ════════════════════════════════════════════════════════════════
print("\n── TEST 4: NSE Bhavcopy Archive ──")

from datetime import timezone, timedelta
IST = timezone(timedelta(hours=5, minutes=30))
today = datetime.now(IST)
dd = today.strftime('%d')
mm = today.strftime('%m')
yyyy = today.strftime('%Y')

urls_to_try = [
    f"https://archives.nseindia.com/products/content/sec_bhavdata_full_{dd}{mm}{yyyy}.csv",
    f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{dd}{mm}{yyyy}.csv",
]

for url in urls_to_try:
    try:
        r6 = requests.get(url, headers=HEADERS, timeout=20)
        print(f"  Trying: {url}")
        print(f"  HTTP {r6.status_code}, length: {len(r6.text)}")
        if r6.status_code == 200 and 'SYMBOL' in r6.text:
            lines = r6.text.strip().split('\n')
            print(f"  ✅ NSE BHAVCOPY WORKS! {len(lines)} rows")
            results['bhavcopy'] = {'status': 'SUCCESS', 'rows': len(lines)}
            break
        else:
            results['bhavcopy'] = {'status': f'HTTP_{r6.status_code}_or_no_data'}
    except Exception as e:
        print(f"  ❌ Error: {e}")
        results['bhavcopy'] = {'status': 'ERROR', 'message': str(e)}

# ════════════════════════════════════════════════════════════════
# VERDICT
# ════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("TEST RESULTS SUMMARY")
print("=" * 60)

for name, result in results.items():
    status = result.get('status', '?')
    icon = '✅' if status == 'SUCCESS' else '❌'
    print(f"  {icon}  {name:20} → {status}")

# Save results to file for artifact upload
with open('fetch_results.json', 'w') as f:
    json.dump(results, f, indent=2)

print("\nFull results (copy and send back):")
print(json.dumps(results, indent=2))
