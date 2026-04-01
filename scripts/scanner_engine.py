#!/usr/bin/env python3
"""
YS TRADING — NSE SCANNER ENGINE v2
Fix: Runs even outside market hours with --force flag for testing
Usage:  python scanner_engine.py           (normal - skips if market closed)
        python scanner_engine.py --force   (force run for testing)
"""

import os, sys, json, time, requests
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')
FORCE_RUN    = '--force' in sys.argv or os.environ.get('FORCE_RUN','') == '1'

IST = timezone(timedelta(hours=5, minutes=30))

NSE_HOLIDAYS = {
    '2026-01-26','2026-04-02','2026-04-03','2026-04-14',
    '2026-05-01','2026-08-15','2026-10-02','2026-11-14','2026-12-25',
}

NIFTY500 = [
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
    "MUTHOOTFIN","RECLTD","PFC","IRFC","ADANIGREEN","SUZLON","VIKRAMSOLR",
    "TATAELXSI","LTIM","MPHASIS","PERSISTENT","COFORGE","KPITTECH","OFSS",
    "LALPATHLAB","APOLLOHOSP","NAUKRI","ZOMATO","INDIAMART","JUBLFOOD",
    "RADICO","MCDOWELL-N","PAGEIND","BATA","CAMS","CDSL","BSE","MCX","ANGELONE",
    "360ONE","HDFCLIFE","SBILIFE","ICICIPRULI","HDFCAMC","UTIAMC","NIPPONLIFE",
    "THERMAX","CUMMINSIND","TIMKEN","SKF","BOSCHLTD","EXIDEIND","TVSMOTOR",
    "ASHOKLEY","TIINDIA","BHARATFORG","JSWENERGY","CESC","TORNTPOWER",
    "ADANIPOWER","NHPC","SJVN","IRCON","RVNL","RAILTEL","WELCORP","RATNAMANI",
    "MANAPPURAM","EQUITASBNK","UJJIVAN","HAVELLS","POLYCAB","KEI","FINOLEX",
    "DIXON","AMBER","KAYNES","MASTEK","BIRLASOFT","HEXAWARE","TANLA","LATENTVIEW",
    "INTELLECT","NEWGEN","ROUTE","HEIDELBERG","JKCEMENT","SHREECEM","ACC",
    "AMBUJACEMENT","RAMCOCEM","DALMIA","GUJGASLTD","PETRONET",
    "DLF","GODREJPROP","OBEROIRLTY","MACROTECH","PRESTIGE","BRIGADE","SOBHA",
    "APOLLOHOSP","HFCL","STERLITE","EASEMYTRIP","LEMONTREE","EIHOTEL",
]
NIFTY500 = list(dict.fromkeys(NIFTY500))

SECTOR_MAP = {
    "RELIANCE":"Energy","TCS":"IT","HDFCBANK":"Banking","INFY":"IT",
    "ICICIBANK":"Banking","HINDUNILVR":"FMCG","SBIN":"Banking","BHARTIARTL":"Telecom",
    "ITC":"FMCG","KOTAKBANK":"Banking","AXISBANK":"Banking","LT":"Infra",
    "WIPRO":"IT","HCLTECH":"IT","ASIANPAINT":"FMCG","MARUTI":"Auto",
    "ULTRACEMCO":"Cement","BAJFINANCE":"Finance","TATAMOTORS":"Auto","ONGC":"Energy",
    "NTPC":"Power","POWERGRID":"Power","TECHM":"IT","SUNPHARMA":"Pharma",
    "DRREDDY":"Pharma","CIPLA":"Pharma","DIVISLAB":"Pharma","BAJAJFINSV":"Finance",
    "TITAN":"Consumer","NESTLEIND":"FMCG","JSWSTEEL":"Metal","TATASTEEL":"Metal",
    "HINDALCO":"Metal","VEDL":"Metal","COALINDIA":"Mining","TATAPOWER":"Power",
    "ADANIENT":"Conglomerate","ADANIPORTS":"Infra","GRASIM":"Cement",
    "EICHERMOT":"Auto","HEROMOTOCO":"Auto","SAIL":"Metal","HINDPETRO":"Energy",
    "BPCL":"Energy","IOC":"Energy","CGPOWER":"Cap Goods","BHEL":"Cap Goods",
    "ABB":"Cap Goods","SIEMENS":"Cap Goods","BANDHANBNK":"Banking",
    "BIOCON":"Pharma","AUROPHARMA":"Pharma","LUPIN":"Pharma","GLENMARK":"Pharma",
    "GAIL":"Gas","IGL":"Gas","MGL":"Gas","JINDALSTEE":"Metal",
    "SBICARD":"Finance","CHOLAFIN":"Finance","MUTHOOTFIN":"Finance",
    "RECLTD":"Finance","PFC":"Finance","IRFC":"Finance",
    "ADANIGREEN":"Energy","SUZLON":"Energy","VIKRAMSOLR":"Energy",
    "TATAELXSI":"IT","LTIM":"IT","MPHASIS":"IT","PERSISTENT":"IT","COFORGE":"IT",
    "KPITTECH":"IT","OFSS":"IT","NAUKRI":"IT","ZOMATO":"Consumer",
    "JSWENERGY":"Power","CESC":"Power","TORNTPOWER":"Power",
    "ADANIPOWER":"Power","NHPC":"Power","SJVN":"Power",
    "HAVELLS":"Cap Goods","POLYCAB":"Cap Goods","KEI":"Cap Goods",
    "DIXON":"Electronics","AMBER":"Electronics","KAYNES":"Electronics",
    "DLF":"Realty","GODREJPROP":"Realty","OBEROIRLTY":"Realty",
    "MACROTECH":"Realty","PRESTIGE":"Realty","BRIGADE":"Realty",
    "JUBLFOOD":"FMCG","RADICO":"FMCG","MCDOWELL-N":"FMCG",
    "APOLLOHOSP":"Pharma","HDFCLIFE":"Finance","SBILIFE":"Finance",
    "ICICIPRULI":"Finance","HDFCAMC":"Finance","UTIAMC":"Finance",
    "MCX":"Finance","BSE":"Finance","CDSL":"Finance","CAMS":"Finance",
    "ANGELONE":"Finance","360ONE":"Finance",
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json',
}

def get_ist():
    n = datetime.now(IST)
    return n, n.hour * 60 + n.minute

def is_market_open():
    now, mins = get_ist()
    if now.weekday() >= 5:
        return False, 'Weekend'
    if now.strftime('%Y-%m-%d') in NSE_HOLIDAYS:
        return False, 'NSE Holiday'
    if mins < 9*60+15:
        return False, 'Pre-market (before 9:15 AM IST)'
    if mins > 11*60+30:
        return False, 'Scan window closed (after 11:30 AM IST)'
    return True, 'Market open'

def fetch_candles(sym):
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}.NS'
    try:
        r = requests.get(url, headers=HEADERS,
                         params={'interval':'5m','range':'1d','includePrePost':'false'},
                         timeout=10)
        if r.status_code != 200:
            return None
        d = r.json()
        res = (d.get('chart',{}).get('result') or [None])[0]
        if not res:
            return None
        meta  = res.get('meta', {})
        q     = res.get('indicators',{}).get('quote',[{}])[0]
        times = res.get('timestamp',[])
        rows = [
            (o,h,l,c,v,t)
            for o,h,l,c,v,t in zip(
                q.get('open',[]),q.get('high',[]),q.get('low',[]),
                q.get('close',[]),q.get('volume',[]),times
            )
            if all(x is not None for x in [o,h,l,c,v])
        ]
        if len(rows) < 3:
            return None
        return {
            'sym':        sym,
            'name':       meta.get('longName', sym),
            'prev_close': meta.get('previousClose') or meta.get('chartPreviousClose') or 0,
            'candles':    rows,
        }
    except Exception:
        return None

def vol_sma(candles, n=20):
    vs = [c[4] for c in candles[-n:] if c[4]]
    return sum(vs)/len(vs) if vs else 0

def scan_stock(sym):
    data = fetch_candles(sym)
    if not data or not data['prev_close']:
        return None
    c = data['candles']
    pc = data['prev_close']
    if len(c) < 2:
        return None
    cur = c[-1]; prv = c[-2]
    o,h,l,cl,v,_ = cur
    _,ph,pl,_,_,_ = prv
    if not all([o,h,l,cl,v,pc]) or cl < 100:
        return None
    avg = vol_sma(c)
    if avg <= 0:
        return None
    vr  = v / avg
    gap = (o - pc) / pc * 100
    rng = (h - l) / cl * 100
    chg = (cl - pc) / pc * 100
    s1 = (abs(gap)<=1.5 and cl>o and cl>ph and 2.0<vr<6.0 and rng<3.0 and chg<4.0)
    s2 = (-3.0<=gap<=0.5 and cl<o and cl<pl and 2.0<vr<6.0 and rng<3.0 and chg>-6.0)
    if s1 or s2:
        return {
            'scanner':  1 if s1 else 2,
            'sym':      sym,
            'name':     data['name'],
            'close':    round(cl,2),
            'chg_pct':  round(chg,2),
            'vol':      int(v),
            'vol_ratio':round(vr,1),
        }
    return None

def supa_post(path, rows, on_conflict=None):
    if not SUPABASE_URL or not SUPABASE_KEY or not rows:
        return False
    hdrs = {
        'Content-Type':'application/json',
        'apikey':SUPABASE_KEY,
        'Authorization':f'Bearer {SUPABASE_KEY}',
        'Prefer':('resolution=merge-duplicates,return=minimal'
                  if on_conflict else 'return=minimal'),
    }
    if on_conflict:
        hdrs['on-conflict'] = on_conflict
    try:
        r = requests.post(f'{SUPABASE_URL}/rest/v1/{path}',
                          headers=hdrs, json=rows, timeout=20)
        return r.status_code in (200,201)
    except Exception as e:
        print(f'  Supabase error: {e}')
        return False

def supa_get(path, params):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    hdrs = {'apikey':SUPABASE_KEY,'Authorization':f'Bearer {SUPABASE_KEY}'}
    try:
        r = requests.get(f'{SUPABASE_URL}/rest/v1/{path}',
                         headers=hdrs,params={'select':'*',**params},timeout=15)
        return r.json() if r.status_code==200 else []
    except:
        return []

def rebuild_freq(date, sc_n):
    rows = supa_get('scan_runs',{'trade_date':f'eq.{date}','scanner':f'eq.{sc_n}','order':'scan_time.asc'})
    if not rows:
        return 0
    bhav = {b['symbol']:b.get('delivery_pct',0) for b in supa_get('bhavcopy',{'trade_date':f'eq.{date}','select':'symbol,delivery_pct'})}
    gmap = {}
    for g in supa_get('nse_live',{'trade_date':f'eq.{date}','select':'symbol'}):
        gmap[g['symbol']] = gmap.get(g['symbol'],0) + 1
    sym_map = {}
    for row in rows:
        s = row['symbol']
        if s not in sym_map:
            sym_map[s] = {'times':[],'name':row.get('stock_name',s),'close':0,'chg':0,'vol':0}
        sym_map[s]['times'].append(row['scan_time'])
        if row.get('close_price'): sym_map[s]['close'] = row['close_price']
        if row.get('change_pct'):  sym_map[s]['chg']   = row['change_pct']
        if row.get('volume'):      sym_map[s]['vol']   = max(sym_map[s]['vol'],row['volume'])
        if row.get('stock_name'):  sym_map[s]['name']  = row['stock_name']

    def streak(times):
        srt=sorted(times); cur=mx=1; last=-999
        for t in srt:
            m=int(t[:2])*60+int(t[3:5])
            cur=(cur+1) if last!=-999 and m-last<=7 else 1
            mx=max(mx,cur); last=m
        return mx

    def score(fr,stk,dp,chg,vol,gf):
        s=fr*25+(stk*15 if stk>=3 else 0)+dp*.5+gf*10
        s+=abs(chg)*3+min((vol/1e5)*.5,25)
        return round(s*10)/10

    ups=[]; now=datetime.now(IST).isoformat()
    for sym,info in sym_map.items():
        fr=len(info['times']); stk=streak(info['times'])
        dp=bhav.get(sym,0); gf=gmap.get(sym,0)
        sc=score(fr,stk,dp,info['chg'],info['vol'],gf)
        ups.append({
            'trade_date':date,'symbol':sym,'stock_name':info['name'],
            'scanner':sc_n,'freq_count':fr,'streak':stk,'max_streak':stk,
            'first_seen':info['times'][0],'last_seen':info['times'][-1],
            'close_price':info['close'],'change_pct':info['chg'],'volume':info['vol'],
            'delivery_pct':dp,'gainer_freq':gf,'score':sc,
            'sector':SECTOR_MAP.get(sym,'Others'),'updated_at':now,
        })
    if ups:
        supa_post('daily_frequency', ups, on_conflict='trade_date,symbol,scanner')
    return len(ups)

def main():
    now_ist, mins = get_ist()
    date     = now_ist.strftime('%Y-%m-%d')
    time_str = now_ist.strftime('%H:%M:%S')

    print(f"\n{'='*60}")
    print(f"YS Scanner  |  {date}  {time_str} IST  |  Force={FORCE_RUN}")
    print(f"{'='*60}")

    ok, reason = is_market_open()
    if not ok and not FORCE_RUN:
        print(f"Skipping: {reason}")
        print("Tip: python scanner_engine.py --force  to test outside market hours")
        return

    if not ok:
        print(f"Market status: {reason}")
        print("FORCE mode ON — scanning anyway\n")

    print(f"Scanning {len(NIFTY500)} stocks with 20 parallel workers...")
    t0 = time.time()

    results = []
    with ThreadPoolExecutor(max_workers=20) as ex:
        futures = {ex.submit(scan_stock,sym):sym for sym in NIFTY500}
        done = 0
        for f in as_completed(futures):
            done += 1
            r = f.result()
            if r:
                results.append(r)
            if done % 100 == 0:
                elapsed = time.time()-t0
                print(f"  {done}/{len(NIFTY500)} done ({elapsed:.0f}s)  |  {len(results)} matches")

    elapsed = time.time()-t0
    s1=[r for r in results if r['scanner']==1]
    s2=[r for r in results if r['scanner']==2]

    print(f"\nFinished in {elapsed:.1f}s")
    print(f"S1 Bullish: {len(s1)} stocks")
    print(f"S2 Bearish: {len(s2)} stocks")

    if s1:
        print("\n📈 S1 (Bullish):")
        for s in sorted(s1,key=lambda x:-x['vol_ratio']):
            print(f"  {s['sym']:15} +{s['chg_pct']:.2f}%  Vol:{s['vol_ratio']:.1f}x  ₹{s['close']}")

    if s2:
        print("\n📉 S2 (Bearish):")
        for s in sorted(s2,key=lambda x:-x['vol_ratio']):
            print(f"  {s['sym']:15} {s['chg_pct']:.2f}%  Vol:{s['vol_ratio']:.1f}x  ₹{s['close']}")

    if not results:
        print("\nNo matches. If market is open this means conditions not triggered yet.")
        print("This is NORMAL — not every 5-min scan will produce matches.")
        return

    if SUPABASE_URL and SUPABASE_KEY:
        scan_rows = [{
            'trade_date':date,'scan_time':time_str,
            'scanner':r['scanner'],'symbol':r['sym'],
            'stock_name':r['name'],'close_price':r['close'],
            'change_pct':r['chg_pct'],'volume':r['vol'],
        } for r in results]
        ok2 = supa_post('scan_runs', scan_rows)
        print(f"\n{'✅' if ok2 else '❌'} scan_runs: {len(scan_rows)} rows saved")
        for sc in [1,2]:
            cnt = rebuild_freq(date, sc)
            if cnt:
                print(f"{'✅'} daily_frequency S{sc}: {cnt} stocks")
    else:
        print("\n⚠ No Supabase credentials — running in dry-run mode only.")
        print("Set SUPABASE_URL and SUPABASE_KEY env vars to save to database.")

if __name__ == '__main__':
    main()
