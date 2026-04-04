"""
YS TRADING — angel_api.py  (FIXED v2)
Uses the official smartapi-python SDK (SmartConnect class)
instead of raw requests — much more reliable.

File: scripts/angel_api.py

REQUIRED ENV VARS (GitHub Secrets):
  ANGEL_API_KEY     — from smartapi.angelone.in developer portal
  ANGEL_CLIENT_CODE — your Angel One client ID (e.g. A12345)
  ANGEL_PASSWORD    — your 4-digit MPIN (trading PIN)
  ANGEL_TOTP_SECRET — plain text token from enable-totp page
"""

import os, time, pyotp
from datetime import datetime, timezone, timedelta
from SmartApi import SmartConnect

IST = timezone(timedelta(hours=5, minutes=30))


class AngelAPI:
    """
    Angel One SmartAPI wrapper using the official SmartConnect SDK.

    Usage:
        api = AngelAPI()
        api.login()
        candles = api.get_candles('NSE', '3045', 'FIVE_MINUTE',
                                  '2026-04-02 09:15', '2026-04-02 11:30')
        api.logout()

    Or as context manager (auto login/logout):
        with AngelAPI() as api:
            candles = api.get_candles(...)
    """

    def __init__(self):
        self.api_key     = os.environ.get('ANGEL_API_KEY', '').strip()
        self.client_code = os.environ.get('ANGEL_CLIENT_CODE', '').strip()
        self.password    = os.environ.get('ANGEL_PASSWORD', '').strip()
        self.totp_secret = os.environ.get('ANGEL_TOTP_SECRET', '').strip()
        self._smart      = None
        self._refresh    = None
        self._feed_token = None

    def _check_config(self):
        missing = []
        if not self.api_key:     missing.append('ANGEL_API_KEY')
        if not self.client_code: missing.append('ANGEL_CLIENT_CODE')
        if not self.password:    missing.append('ANGEL_PASSWORD')
        if not self.totp_secret: missing.append('ANGEL_TOTP_SECRET')
        if missing:
            raise ValueError(
                f"Missing credentials: {missing}\n"
                "Add them as GitHub Secrets."
            )

    def login(self) -> bool:
        """Login using SmartConnect SDK. Returns True on success."""
        self._check_config()

        # Generate TOTP
        try:
            totp = pyotp.TOTP(self.totp_secret).now()
        except Exception as e:
            raise ValueError(
                f"Bad TOTP secret: {e}\n"
                "Visit https://smartapi.angelbroking.com/enable-totp\n"
                "Copy the plain TEXT token shown below the QR code."
            )

        # Create SmartConnect object and login
        self._smart = SmartConnect(api_key=self.api_key)
        data = self._smart.generateSession(self.client_code, self.password, totp)

        if not data or not data.get('status'):
            msg = data.get('message', 'Unknown error') if data else 'Empty response'
            raise ConnectionError(
                f"Angel One login failed: {msg}\n"
                "Check: ANGEL_CLIENT_CODE, ANGEL_PASSWORD (4-digit MPIN), ANGEL_API_KEY"
            )

        self._refresh    = data['data']['refreshToken']
        self._feed_token = self._smart.getfeedToken()

        print(f"Login OK — client: {self.client_code}")
        return True

    def logout(self):
        """Clean logout."""
        try:
            if self._smart and self.client_code:
                self._smart.terminateSession(self.client_code)
        except Exception:
            pass

    def get_candles(self, exchange, token, interval, from_dt, to_dt) -> list:
        """
        Fetch OHLCV candles.
        Returns list of [timestamp, open, high, low, close, volume]

        Args:
            exchange: 'NSE' or 'BSE'
            token:    Angel One token (e.g. '3045' for SBIN)
            interval: 'ONE_MINUTE','FIVE_MINUTE','FIFTEEN_MINUTE','ONE_HOUR','ONE_DAY'
            from_dt:  'YYYY-MM-DD HH:MM'
            to_dt:    'YYYY-MM-DD HH:MM'
        """
        if not self._smart:
            raise RuntimeError("Not logged in. Call api.login() first.")

        params = {
            'exchange':    exchange,
            'symboltoken': token,
            'interval':    interval,
            'fromdate':    from_dt,
            'todate':      to_dt,
        }
        try:
            data = self._smart.getCandleData(params)
            if data and data.get('status') and data.get('data'):
                return data['data']
            return []
        except Exception:
            return []

    def get_today_candles(self, exchange, token, interval='FIVE_MINUTE') -> list:
        """Get all candles for today from 9:15 AM to now."""
        ist_now = datetime.now(IST)
        date_str = ist_now.strftime('%Y-%m-%d')
        return self.get_candles(
            exchange, token, interval,
            f'{date_str} 09:15',
            ist_now.strftime('%Y-%m-%d %H:%M')
        )

    def get_profile(self) -> dict:
        """Get user profile (verifies login is working)."""
        if not self._smart or not self._refresh:
            return {}
        try:
            data = self._smart.getProfile(self._refresh)
            return data.get('data', {}) if data and data.get('status') else {}
        except Exception:
            return {}

    def get_quote(self, exchange, tokens: list) -> dict:
        """
        Get live quote for up to 50 tokens at once.
        Returns dict: {token: {ltp, open, high, low, close, volume}}
        """
        if not self._smart:
            return {}
        try:
            data = self._smart.getMarketData('FULL', {exchange: tokens[:50]})
            if not data or not data.get('status') or not data.get('data'):
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
        except Exception:
            return {}

    def __enter__(self):
        self.login()
        return self

    def __exit__(self, *_):
        self.logout()


# ── Simple rate limiter ────────────────────────────────────────
class RateLimiter:
    """
    Angel One allows ~100 historical requests/minute.
    Usage: call limiter.wait() before each get_candles() call.
    """
    def __init__(self, max_per_min=85):
        self.max_per_min = max_per_min
        self._calls = []

    def wait(self):
        now = time.time()
        self._calls = [t for t in self._calls if now - t < 60]
        if len(self._calls) >= self.max_per_min:
            sleep_for = 60 - (now - self._calls[0]) + 0.1
            if sleep_for > 0:
                time.sleep(sleep_for)
        self._calls.append(time.time())
