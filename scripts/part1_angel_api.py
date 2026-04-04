"""
YS TRADING — angel_api.py
Angel One SmartAPI wrapper — secure, reusable, handles all auth
File: scripts/angel_api.py

To add a new data type in future: add one method to AngelAPI class.
Everything else (scanner, cleanup, strategies) uses this one file.

REQUIRED ENV VARS (GitHub Secrets):
  ANGEL_API_KEY     — from smartapi.angelone.in developer portal
  ANGEL_CLIENT_CODE — your Angel One client ID (e.g. A12345)
  ANGEL_PASSWORD    — your Angel One MPIN / trading password
  ANGEL_TOTP_SECRET — plain text from enable-totp page (not the QR image)
"""

import os, time, json, requests, pyotp
from datetime import datetime, timezone, timedelta

# ── CONSTANTS ─────────────────────────────────────────────────
BASE_URL   = 'https://apiconnect.angelbroking.com'
LOGIN_URL  = f'{BASE_URL}/rest/pub/angelbroking/user/v1/loginByPassword'
LOGOUT_URL = f'{BASE_URL}/rest/secure/angelbroking/user/v1/logout'
HIST_URL   = f'{BASE_URL}/rest/secure/angelbroking/historical/v2/getCandleData'
QUOTE_URL  = f'{BASE_URL}/rest/secure/angelbroking/market/v1/quote/'
PROFILE_URL= f'{BASE_URL}/rest/secure/angelbroking/user/v1/getProfile'

IST = timezone(timedelta(hours=5, minutes=30))
SYMBOL_MASTER_URL = 'https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json'

# Intervals supported by Angel One historical API
INTERVALS = ['ONE_MINUTE','THREE_MINUTE','FIVE_MINUTE','TEN_MINUTE',
             'FIFTEEN_MINUTE','THIRTY_MINUTE','ONE_HOUR','ONE_DAY']


class AngelAPI:
    """
    Angel One SmartAPI wrapper.
    Usage:
        api = AngelAPI()
        api.login()
        candles = api.get_candles('NSE', '3045', 'FIVE_MINUTE', '2026-04-02 09:15', '2026-04-02 11:30')
        api.logout()
    """

    def __init__(self):
        self.api_key     = os.environ.get('ANGEL_API_KEY', '')
        self.client_code = os.environ.get('ANGEL_CLIENT_CODE', '')
        self.password    = os.environ.get('ANGEL_PASSWORD', '')
        self.totp_secret = os.environ.get('ANGEL_TOTP_SECRET', '')
        self.auth_token  = None
        self.feed_token  = None
        self.refresh_token = None
        self._session    = requests.Session()
        self._session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-UserType': 'USER',
            'X-SourceID': 'WEB',
            'X-ClientLocalIP': '127.0.0.1',
            'X-ClientPublicIP': '127.0.0.1',
            'X-MACAddress': '00:00:00:00:00:00',
        })

    def _check_config(self):
        missing = [k for k in ['api_key','client_code','password','totp_secret']
                   if not getattr(self, k)]
        if missing:
            raise ValueError(
                f"Missing Angel One credentials: {missing}\n"
                "Set these as GitHub Secrets: ANGEL_API_KEY, ANGEL_CLIENT_CODE, "
                "ANGEL_PASSWORD, ANGEL_TOTP_SECRET"
            )

    def login(self) -> bool:
        """Login to Angel One. Returns True on success."""
        self._check_config()
        try:
            totp = pyotp.TOTP(self.totp_secret).now()
        except Exception as e:
            raise ValueError(f"Invalid TOTP secret: {e}\n"
                             "Visit: https://smartapi.angelbroking.com/enable-totp")

        payload = {
            'clientcode': self.client_code,
            'password':   self.password,
            'totp':       totp,
        }
        headers = {**self._session.headers, 'X-PrivateKey': self.api_key}
        resp = self._session.post(LOGIN_URL, json=payload, headers=headers, timeout=15)
        data = resp.json()

        if not data.get('status'):
            msg = data.get('message', 'Login failed')
            raise ConnectionError(f"Angel One login failed: {msg}")

        d = data['data']
        self.auth_token    = d['jwtToken']
        self.refresh_token = d['refreshToken']
        self.feed_token    = d.get('feedToken', '')
        # Update session headers with auth
        self._session.headers.update({
            'Authorization': f'Bearer {self.auth_token}',
            'X-PrivateKey':  self.api_key,
            'X-ClientCode':  self.client_code,
            'X-FeedToken':   self.feed_token,
        })
        print(f"✅ Angel One login OK — {self.client_code}")
        return True

    def logout(self):
        """Clean logout."""
        try:
            payload = {'clientcode': self.client_code, 'feedToken': self.feed_token}
            self._session.post(LOGOUT_URL, json=payload, timeout=10)
        except Exception:
            pass

    def get_candles(self, exchange, token, interval, from_dt, to_dt) -> list:
        """
        Fetch OHLCV candles.
        Returns list of [timestamp, open, high, low, close, volume]
        
        Args:
            exchange:  'NSE' or 'NFO' or 'BSE'
            token:     Angel One symbol token (e.g. '3045' for SBIN)
            interval:  'ONE_MINUTE', 'FIVE_MINUTE', etc.
            from_dt:   'YYYY-MM-DD HH:MM'
            to_dt:     'YYYY-MM-DD HH:MM'
        
        Example:
            candles = api.get_candles('NSE', '3045', 'FIVE_MINUTE',
                                      '2026-04-02 09:15', '2026-04-02 11:30')
        """
        if not self.auth_token:
            raise RuntimeError("Not logged in. Call api.login() first.")

        payload = {
            'exchange':    exchange,
            'symboltoken': token,
            'interval':    interval,
            'fromdate':    from_dt,
            'todate':      to_dt,
        }
        resp = self._session.post(HIST_URL, json=payload, timeout=15)
        data = resp.json()

        if not data.get('status'):
            # Token/exchange mismatch or rate limit — return empty, don't crash
            return []

        return data.get('data', []) or []

    def get_today_candles(self, exchange, token, interval='FIVE_MINUTE') -> list:
        """
        Convenience: get all candles for today from 9:15 to now.
        Returns list of [timestamp, open, high, low, close, volume]
        """
        ist_now = datetime.now(IST)
        date_str = ist_now.strftime('%Y-%m-%d')
        from_dt = f'{date_str} 09:15'
        to_dt   = ist_now.strftime('%Y-%m-%d %H:%M')
        return self.get_candles(exchange, token, interval, from_dt, to_dt)

    def get_quote(self, exchange, tokens: list) -> dict:
        """
        Get live quote (LTP + OHLCV) for multiple tokens.
        Returns dict: {token: {ltp, open, high, low, close, volume}}
        Max 50 tokens per call.
        """
        if not self.auth_token:
            raise RuntimeError("Not logged in.")

        payload = {'mode': 'FULL', 'exchangeTokens': {exchange: tokens[:50]}}
        resp = self._session.post(QUOTE_URL, json=payload, timeout=10)
        data = resp.json()
        if not data.get('status') or not data.get('data'):
            return {}

        result = {}
        for item in data['data'].get('fetched', []):
            result[item['symbolToken']] = {
                'ltp':    item.get('ltp', 0),
                'open':   item.get('open', 0),
                'high':   item.get('high', 0),
                'low':    item.get('low', 0),
                'close':  item.get('close', 0),
                'volume': item.get('tradeVolume', 0),
                'symbol': item.get('tradingSymbol', ''),
            }
        return result

    def get_profile(self) -> dict:
        """Get user profile (used to verify login)."""
        if not self.auth_token:
            return {}
        resp = self._session.post(PROFILE_URL,
                                  json={'refreshToken': self.refresh_token},
                                  timeout=10)
        data = resp.json()
        return data.get('data', {}) if data.get('status') else {}

    def __enter__(self):
        self.login()
        return self

    def __exit__(self, *_):
        self.logout()


# ── RATE LIMITER ──────────────────────────────────────────────
class RateLimiter:
    """
    Simple rate limiter for Angel One historical API.
    Angel One allows ~100 requests/minute.
    Use: limiter.wait() before each request.
    """
    def __init__(self, max_per_min=90):
        self.max_per_min = max_per_min
        self._calls = []

    def wait(self):
        now = time.time()
        # Remove calls older than 60 seconds
        self._calls = [t for t in self._calls if now - t < 60]
        if len(self._calls) >= self.max_per_min:
            sleep_for = 60 - (now - self._calls[0]) + 0.1
            if sleep_for > 0:
                time.sleep(sleep_for)
        self._calls.append(time.time())
