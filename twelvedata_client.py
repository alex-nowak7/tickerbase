"""
Twelve Data adapter for Tickerbase.

Supplies the daily price history that yfinance used to provide. Free Basic plan
covers this comfortably: 800 API credits/day, 8/minute, and one history call
costs exactly 1 credit.

  - Get a key:  https://twelvedata.com/register  (no card)
  - The key arrives from the Cloudflare secret store via set_api_key().

get_history() returns a PriceHistory object shaped like the pandas DataFrame
that price_stats() in tickerbase.py already expects: .empty, .columns, .index,
and hist["Close"].tolist(). No pandas required.
"""

import datetime as _dt

import requests

BASE = "https://api.twelvedata.com"
TIMEOUT = 8       # seconds
DAYS = 520        # ~2 trading years

TD_KEY = ""


def set_api_key(key):
    global TD_KEY
    if key:
        TD_KEY = str(key)


try:  # local dev fallback only
    import os
    TD_KEY = os.environ.get("TWELVEDATA_API_KEY", "")
except Exception:
    pass


# ============================================================
#  Minimal DataFrame stand-in
# ============================================================
class _Column:
    """One column of numbers. Only .tolist() is ever used."""

    def __init__(self, values):
        self._values = values

    def tolist(self):
        return list(self._values)

    def __len__(self):
        return len(self._values)

    def __iter__(self):
        return iter(self._values)


class PriceHistory:
    """Quacks like the price DataFrame price_stats() expects."""

    def __init__(self, dates, closes):
        self.index = dates          # list of datetime.date (have .strftime)
        self._closes = closes
        self.columns = ["Close"]

    @property
    def empty(self):
        return not self._closes

    def __getitem__(self, key):
        if key == "Close":
            return _Column(self._closes)
        raise KeyError(key)

    def __len__(self):
        return len(self._closes)


# ============================================================
#  get_history — replaces yfinance Ticker.history()
# ============================================================
def get_history(ticker):
    """Return ~2 years of daily closes as a PriceHistory, or None on any failure.
    Never raises: callers treat None as 'no price data'."""
    if not TD_KEY:
        return None
    symbol = (ticker or "").upper().strip()
    if not symbol:
        return None

    try:
        r = requests.get(
            BASE + "/time_series",
            params={"symbol": symbol, "interval": "1day",
                    "outputsize": DAYS, "apikey": TD_KEY},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            return None
        payload = r.json()
    except Exception:
        return None

    # Twelve Data reports errors in the body with status "error", not via HTTP code.
    if not isinstance(payload, dict) or payload.get("status") == "error":
        return None

    rows = payload.get("values")
    if not rows or not isinstance(rows, list):
        return None

    # Twelve Data returns newest first; price_stats wants oldest first.
    dates, closes = [], []
    for row in reversed(rows):
        try:
            c = float(row.get("close"))
        except (TypeError, ValueError):
            continue
        ds = str(row.get("datetime") or "")[:10]
        try:
            d = _dt.date(int(ds[0:4]), int(ds[5:7]), int(ds[8:10]))
        except (ValueError, IndexError):
            continue
        dates.append(d)
        closes.append(c)

    if len(closes) < 20:      # price_stats needs 20+ points anyway
        return None
    return PriceHistory(dates, closes)
