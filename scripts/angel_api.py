"""
YS TRADING — angel_api.py (FIXED v5)
- Added get_profile()
- Fixed RateLimiter visibility for test_angel.py
"""

import os, time, pyotp
from datetime import datetime, timezone, timedelta
from SmartApi import SmartConnect

IST = timezone(timedelta(hours=5, minutes=30))

# ── Rate Limiter (Moved here so test_angel.py can import it) ───────────
class RateLimiter:
    """
    Angel One allows ~100 historical requests/minute.
    """
    def __init__(self, max_per_min=85):
        self.max_per_min = max_per_min
        self._calls = []

    def wait(self):
        now = time.time()
        self._calls = [t for t in self._calls if now - t < 60]
        if len(self._calls) >= self.max_per_min:
            sleep_for = 60 - (now - self._calls[0])
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._calls = [t for t in self._calls if time.time() - t < 60]
        self._calls.append(time.time())


class AngelAPI:
    def __init__(self):
        self.api_key      = os.environ.get('ANGEL_API_KEY', '').strip()
        self.client_code  = os.environ.get('ANGEL_CLIENT_CODE', '').strip()
        self.password     = os.environ.get('ANGEL_PASSWORD', '').strip()
        self.totp_secret  = os.environ.get('ANGEL_TOTP_SECRET', '').strip()
        self._smart       = None
        
        # Public attributes expected by the test script
        self.auth_token    = None 
        self.refresh_token = None
        self.feed_token    = None

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

        self.auth_token    = data['data']['jwtToken']
        self.refresh_token = data['data']['refreshToken']
        self.feed_token    = self._smart.getfeedToken()

        print(f"Login OK — client: {self.client_code}")
        return True

    def get_profile(self):
        """Fetch user profile details (Expected by test_angel.py)"""
        if not self._smart:
            return None
        return self._smart.getProfile(self.refresh_token)

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
