"""
YS TRADING — symbol_master.py
NSE symbol → Angel One token lookup
File: scripts/symbol_master.py

Angel One API requires a numeric token (e.g. '3045' for SBIN) not just the symbol name.
This file downloads the master list once and caches it locally.

Usage:
    from symbol_master import SymbolMaster
    sm = SymbolMaster()
    token = sm.get_token('SBIN')     # returns '3045'
    info  = sm.get_info('SBIN')      # returns {token, symbol, name, exch_seg}
    all_tokens = sm.get_nifty500_tokens()  # returns list of (sym, token) tuples
"""

import os, json, requests, time
from pathlib import Path

MASTER_URL   = 'https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json'
CACHE_FILE   = Path('/tmp/ys_symbol_master.json')
CACHE_HOURS  = 12   # refresh every 12 hours

# Complete Nifty 500 universe with NSE symbols
# These are the symbols we scan every 5 minutes
NIFTY500_SYMBOLS = [
    # Large Cap — Nifty 50
    "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","HINDUNILVR","SBIN","BHARTIARTL",
    "ITC","KOTAKBANK","AXISBANK","LT","WIPRO","HCLTECH","ASIANPAINT","MARUTI",
    "ULTRACEMCO","BAJFINANCE","TATAMOTORS","ONGC","NTPC","POWERGRID","TECHM",
    "SUNPHARMA","DRREDDY","CIPLA","DIVISLAB","BAJAJFINSV","TITAN","NESTLEIND",
    "JSWSTEEL","TATASTEEL","HINDALCO","VEDL","COALINDIA","TATAPOWER","ADANIENT",
    "ADANIPORTS","GRASIM","EICHERMOT","HEROMOTOCO","M&M","BAJAJ-AUTO","TATACONSUM",
    "BRITANNIA","GODREJCP","DABUR",

    # Mid Cap
    "SAIL","NMDC","HINDPETRO","BPCL","IOC","CGPOWER","BHEL","ABB","SIEMENS",
    "BANDHANBNK","IDFCFIRSTB","FEDERALBNK","INDUSINDBK","BIOCON","AUROPHARMA",
    "LUPIN","GLENMARK","ALKEM","TORNTPHARM","IPCALAB","GAIL","IGL","MGL",
    "JINDALSTEE","HINDCOPPER","NATIONALUM","SBICARD","CHOLAFIN","MUTHOOTFIN",
    "RECLTD","PFC","IRFC","ADANIGREEN","SUZLON","VIKRAMSOLR",

    # IT & Tech
    "TATAELXSI","LTIM","MPHASIS","PERSISTENT","COFORGE","KPITTECH","OFSS",
    "MASTEK","BIRLASOFT","HEXAWARE","TANLA","LATENTVIEW","INTELLECT","NEWGEN",
    "ROUTE","RATEGAIN","ZAGGLE","NAUKRI","ZOMATO","INDIAMART","JUSTDIAL",

    # Pharma & Healthcare
    "LALPATHLAB","METROPOLIS","APOLLOHOSP","FORTIS","MAXHEALTH","RAINBOW",
    "KRSNAA","VIJAYA","ASTERDM","YATHARTH","SHALBY","SYNGENE","GRANULES",

    # FMCG & Consumer
    "PAGEIND","RAYMOND","ADITBIRLAF","BATA","RELAXO","JUBLFOOD","WESTLIFE",
    "DEVYANI","SAPPHIRE","BIKAJI","RADICO","MCDOWELL-N","SULA","VSTIND",
    "TVSMOTOR",

    # Power & Energy
    "JSWENERGY","CESC","TORNTPOWER","ADANIPOWER","NHPC","SJVN","TATAPOWER",
    "RPOWER","INDIGRID","POWERINDIA",

    # Finance & Insurance
    "CAMS","CDSL","BSE","MCX","ANGELONE","IIFLWAM","MOTILALOFS","MOFSL",
    "360ONE","NUVAMA","EDELWEISS","IIFL","BAJAJHFL","APTUS","HOMEFIRST",
    "AAVAS","REPCO","CANFINHOME","HDFCLIFE","SBILIFE","ICICIPRULI","HDFCAMC",
    "UTIAMC","NIPPONLIFE","GICRE","NIACL","STARHEALTH","MANAPPURAM","EQUITASBNK",
    "UJJIVAN","CREDITACC","SPANDANA","PAISALO","MASFIN","KFINTECH",

    # Infra & Construction
    "GMRAIRPORT","CONCOR","IRCON","RVNL","RAILTEL","CAPACITE","VRL","MAHLOG",
    "BLUEDART","GATI","ALLCARGO","MAHSEAMLES",

    # Realty
    "DLF","GODREJPROP","OBEROIRLTY","MACROTECH","PRESTIGE","BRIGADE","SOBHA",
    "PHOENIXLTD","NESCO","SUNTECK","KOLTEPATIL",

    # Auto & Auto Ancillary
    "ESCORTS","FORCE","ASHOKLEY","TIINDIA","BHARATFORG","RAMKRISHNA","BOSCHLTD",
    "EXIDEIND","MOTHERSON","MINDA","SUNDRMFAST","SUPRAJIT","TVSMOTOR",

    # Metal & Mining
    "MOIL","GMDC","RATNAMANI","WELCORP","APL",

    # Capital Goods
    "THERMAX","CUMMINSIND","GREAVES","ELGIEQUIP","KSB","TIMKEN","SKF",
    "SCHAEFFLER","HAVELLS","POLYCAB","KEI","FINOLEX","HBLPOWER","DIXON",
    "AMBER","PGEL","KAYNES","SYRMA","AVALON","ELIN","CENTUM",

    # Cement
    "ACC","AMBUJACEMENT","RAMCOCEM","INDIACEM","DALMIA","PRISMJOH",
    "HEIDELBERG","BIRLACORPN","JKCEMENT","SHREECEM","NUVOCO",

    # Gas
    "GUJGASLTD","PETRONET","AEGASCHEM","CLEAN",

    # Hotels & Travel
    "EASEMYTRIP","IXIGO","THOMASCOOK","CHALET","JUNIPER","LEMONTREE",
    "EIHOTEL","MAHINDHOTEL","TAJGVK",

    # Media
    "ZEEL","SUNTV","NETWORK18","TV18BRDCST",

    # Chemicals
    "PIDILITIND","DEEPAKNTR","GSFC","HERANBA",

    # Telecom
    "HFCL","STERLITE","VINDHYATEL","ITI",
]

# Deduplicate while preserving order
_seen = set()
NIFTY500_SYMBOLS = [s for s in NIFTY500_SYMBOLS if not (s in _seen or _seen.add(s))]


class SymbolMaster:
    """
    Downloads and caches the Angel One symbol master file.
    Provides fast token lookup by NSE symbol name.
    """

    def __init__(self, force_refresh=False):
        self._data = {}   # symbol → {token, name, exch_seg, tick_size}
        self._load(force_refresh)

    def _load(self, force_refresh=False):
        # Use cache if fresh enough
        if not force_refresh and CACHE_FILE.exists():
            age_hours = (time.time() - CACHE_FILE.stat().st_mtime) / 3600
            if age_hours < CACHE_HOURS:
                try:
                    with open(CACHE_FILE) as f:
                        self._data = json.load(f)
                    print(f"Symbol master: {len(self._data)} symbols (from cache)")
                    return
                except Exception:
                    pass

        print("Downloading Angel One symbol master...")
        try:
            resp = requests.get(MASTER_URL, timeout=30)
            resp.raise_for_status()
            raw = resp.json()
        except Exception as e:
            print(f"Failed to download symbol master: {e}")
            # Try to use stale cache as fallback
            if CACHE_FILE.exists():
                with open(CACHE_FILE) as f:
                    self._data = json.load(f)
                print(f"Using stale cache: {len(self._data)} symbols")
            return

        # Build lookup: NSE EQ symbols only
        # Format in master: symbol='SBIN-EQ', name='SBIN', token='3045'
        parsed = {}
        for item in raw:
            exch = item.get('exch_seg', '')
            sym  = item.get('symbol', '')
            if exch == 'NSE' and sym.endswith('-EQ'):
                # Clean name: 'SBIN-EQ' → 'SBIN'
                clean = sym.replace('-EQ', '')
                parsed[clean] = {
                    'token':     item.get('token', ''),
                    'symbol':    sym,           # full: 'SBIN-EQ'
                    'name':      item.get('name', clean),
                    'exch_seg':  exch,
                    'tick_size': item.get('tick_size', '5'),
                    'lot_size':  item.get('lotsize', '1'),
                }

        self._data = parsed
        # Save cache
        CACHE_FILE.parent.mkdir(exist_ok=True)
        with open(CACHE_FILE, 'w') as f:
            json.dump(parsed, f)
        print(f"Symbol master: {len(parsed)} NSE EQ symbols downloaded and cached")

    def get_token(self, symbol: str) -> str:
        """Get Angel One token for a symbol. Returns '' if not found."""
        info = self._data.get(symbol.upper(), {})
        return info.get('token', '')

    def get_info(self, symbol: str) -> dict:
        """Get full info for a symbol."""
        return self._data.get(symbol.upper(), {})

    def get_nifty500_tokens(self) -> list:
        """
        Returns list of (symbol, token) tuples for all NIFTY500 symbols
        that exist in Angel One master. Skips symbols not found.
        """
        result = []
        not_found = []
        for sym in NIFTY500_SYMBOLS:
            token = self.get_token(sym)
            if token:
                result.append((sym, token))
            else:
                not_found.append(sym)
        if not_found:
            print(f"  Symbols not in master ({len(not_found)}): {not_found[:10]}...")
        print(f"  Valid tokens: {len(result)} / {len(NIFTY500_SYMBOLS)}")
        return result

    def get_all(self) -> dict:
        """Return the full symbol map."""
        return self._data
