"""
YS TRADING — angel_api.py
Thread-safe version for Parallel Scanning.
"""
import os, time, pyotp
from datetime import datetime, timezone, timedelta
from SmartApi import SmartConnect

class RateLimiter:
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
        self.limiter      = RateLimiter(max_per_min=85)

    def login(self) -> bool:
        totp = pyotp.TOTP(self.totp_secret).now()
        self._smart = SmartConnect(api_key=self.api_key)
        data = self._smart.generateSession(self.client_code, self.password, totp)
        if not data or not data.get('status'):
            raise ConnectionError("Angel One login failed")
        self.refresh_token = data['data']['refreshToken']
        return True

    def get_profile(self):
        if not self._smart: return None
        return self._smart.getProfile(self.refresh_token)

    def get_candles(self, exchange, token, interval, from_dt, to_dt):
        self.limiter.wait() # MENTOR NOTE: Shared across all threads
        params = {
            'exchange': exchange, 'symboltoken': token,
            'interval': interval, 'fromdate': from_dt, 'todate': to_dt
        }
        try:
            data = self._smart.getCandleData(params)
            return data['data'] if data and data.get('status') else []
        except:
            return []

    def logout(self):
        try:
            if self._smart and self.client_code:
                self._smart.terminateSession(self.client_code)
        except: pass
