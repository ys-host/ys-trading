"""
YS TRADING — angel_api.py  (FIXED v3)
Fixed the 'auth_token' AttributeError that was causing 
the test script to report a false login failure.
"""

import os, time, pyotp
from datetime import datetime, timezone, timedelta
from SmartApi import SmartConnect

IST = timezone(timedelta(hours=5, minutes=30))


class AngelAPI:
    def __init__(self):
        self.api_key     = os.environ.get('ANGEL_API_KEY', '').strip()
        self.client_code = os.environ.get('ANGEL_CLIENT_CODE', '').strip()
        self.password    = os.environ.get('ANGEL_PASSWORD', '').strip()
        self.totp_secret = os.environ.get('ANGEL_TOTP_SECRET', '').strip()
        self._smart      = None
        self._refresh    = None
        self._feed_token = None
        self.auth_token  = None  # Added: placeholder for the JWT token

    def _check_config(self):
        missing = []
        if not self.api_key:     missing.append('ANGEL_API_KEY')
        if not self.client_code: missing.append('ANGEL_CLIENT_CODE')
        if not self.password:    missing.append('ANGEL_PASSWORD')
        if not self.totp_secret: missing.append('ANGEL_TOTP_SECRET')
        if missing:
            raise ValueError(f"Missing credentials: {missing}")

    def login(self) -> bool:
        """Login using SmartConnect SDK."""
        self._check_config()

        try:
            totp = pyotp.TOTP(self.totp_secret).now()
        except Exception as e:
            raise ValueError(f"Bad TOTP secret: {e}")

        self._smart = SmartConnect(api_key=self.api_key)
        data = self._smart.generateSession(self.client_code, self.password, totp)

        if not data or not data.get('status'):
            msg = data.get('message', 'Unknown error') if data else 'Empty response'
            raise ConnectionError(f"Angel One login failed: {msg}")

        # FIX: Explicitly store the tokens that the test script expects
        self.auth_token  = data['data']['jwtToken']
        self._refresh    = data['data']['refreshToken']
        self._feed_token = self._smart.getfeedToken()

        print(f"Login OK — client: {self.client_code}")
        return True

    def logout(self):
        try:
            if self._smart and self.client_code:
                self._smart.terminateSession(self.client_code)
        except Exception:
            pass

    def get_candles(self, exchange, token, interval, from_dt, to_dt) -> list:
        if not self._smart:
            raise RuntimeError("Not logged in.")

        params = {
            'exchange':    exchange,
            'symboltoken': token,
            'interval':    interval,
            'fromdate':    from_dt,
            'todate':      to_dt,
        }
        try:
            data = self._smart.getCandleData(params)
            return data['data'] if data and data.get('status') else []
        except Exception:
            return []

    def __enter__(self):
        self.login()
        return self

    def __exit__(self, *_):
        self.logout()
