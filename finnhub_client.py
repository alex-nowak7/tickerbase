"""
Finnhub adapter for Tickerbase.

Replaces the heaviest yfinance calls (.info + peer info fetches) with Finnhub
API calls that work reliably from cloud server IPs like Render. The renderer
in tickerbase.py doesn't change — each function here returns data in the same
shape yfinance returns, so the swap is surgical.

Free Finnhub tier covers everything in this file (60 calls/min, no card).
  • Get a key:  https://finnhub.io/dashboard
  • Set env var FINNHUB_API_KEY before calling any function in this module.
"""

import time
import math

import requests

# On Cloudflare Workers there is no os.environ - secrets arrive on the `env`
# object handed to fetch(). The Worker entrypoint calls set_api_key() once per
# request; locally you can still export FINNHUB_API_KEY instead.
FINNHUB_KEY = ""


def set_api_key(key):
    global FINNHUB_KEY
    if key:
        FINNHUB_KEY = str(key)


try:  # local dev fallback only
    import os
    FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")
except Exception:
    pass

BASE = "https://finnhub.io/api/v1"
TIMEOUT = 8  # seconds per HTTP request



# ============================================================
#  Minimal table type — replaces pandas.DataFrame
# ============================================================
# The renderer only ever uses three things: .empty, .iloc[0], and
# .head(n).iterrows(). pandas dragged in numpy and ~15 other packages
# for that, so this stands in for it.
class Table:
    def __init__(self, rows):
        self._rows = [dict(r) for r in rows]

    @property
    def empty(self):
        return not self._rows

    @property
    def iloc(self):
        return self._rows          # supports iloc[0]

    def head(self, n):
        return Table(self._rows[:n])

    def iterrows(self):
        return enumerate(self._rows)

    def __len__(self):
        return len(self._rows)

    def __iter__(self):
        return iter(self._rows)

# ============================================================
#  HTTP plumbing — every external call goes through here
# ============================================================
def _get(path, params=None, retries=1):
    """One GET to Finnhub. Returns parsed JSON, or None on any failure.
    Never raises — callers can treat None as 'no data'."""
    if not FINNHUB_KEY:
        return None
    p = dict(params or {})
    p["token"] = FINNHUB_KEY
    for attempt in range(retries + 1):
        try:
            r = requests.get(BASE + path, params=p, timeout=TIMEOUT)
            if r.status_code == 429:               # rate-limited; brief backoff and retry
                time.sleep(0.7 * (attempt + 1))
                continue
            if r.status_code != 200:
                return None
            return r.json()
        except Exception:
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
                continue
            return None
    return None


# ============================================================
#  Small helpers — keep number handling consistent
# ============================================================
def _f(x):
    """Float or None, coercing strings, rejecting NaN/inf."""
    if x is None:
        return None
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except (TypeError, ValueError):
        return None


def _pct_to_dec(x):
    """Finnhub returns margins/yields as percentages (25.0); yfinance returns
    them as decimals (0.25). Divide by 100 so the renderer sees yfinance's format."""
    v = _f(x)
    return None if v is None else v / 100.0


# ============================================================
#  Metric key mapping:  yfinance info key  ->  Finnhub metric key
# ============================================================
# Direct numeric pass-through (no unit conversion needed).
_METRIC_DIRECT = {
    # valuation
    "trailingPE":                    "peBasicExclExtraTTM",
    "priceToBook":                   "pbAnnual",
    "priceToSalesTrailing12Months":  "psTTM",
    "enterpriseToEbitda":            "evEbitdaTTM",
    "forwardPE":                     "forwardPE",
    "pegRatio":                      "pegTTM",
    "trailingPegRatio":              "pegTTM",
    # risk / capital structure
    "beta":                          "beta",
    "currentRatio":                  "currentRatioAnnual",
    "quickRatio":                    "quickRatioAnnual",
    # earnings / market
    "trailingEps":                   "epsTTM",
    "bookValue":                     "bookValuePerShareAnnual",
    "fiftyTwoWeekHigh":              "52WeekHigh",
    "fiftyTwoWeekLow":               "52WeekLow",
}

# These come from Finnhub as percentages — divide by 100 to match yfinance shape.
_METRIC_PCT = {
    "grossMargins":      "grossMarginTTM",
    "operatingMargins":  "operatingMarginTTM",
    "profitMargins":     "netProfitMarginTTM",
    "returnOnEquity":    "roeTTM",
    "returnOnAssets":    "roaTTM",
    "revenueGrowth":     "revenueGrowthTTMYoy",
    "earningsGrowth":    "epsGrowthTTMYoy",
    "dividendYield":     "dividendYieldIndicatedAnnual",
    "payoutRatio":       "payoutRatioTTM",
}


# ============================================================
#  Industry -> sector
# ============================================================
# Finnhub publishes a single "finnhubIndustry" field, which is industry-level
# (e.g. "Semiconductors"). Mapping it up to a GICS-style sector gives the two
# distinct labels the report expects. Unknown industries return None, and the
# report just shows the industry alone.
_SECTOR_BY_INDUSTRY = {
    # Information Technology
    "semiconductors": "Information Technology",
    "software": "Information Technology",
    "technology": "Information Technology",
    "electronic equipment": "Information Technology",
    "hardware": "Information Technology",
    "it services": "Information Technology",
    # Communication Services
    "media": "Communication Services",
    "telecommunication": "Communication Services",
    "communications": "Communication Services",
    "internet": "Communication Services",
    "advertising & marketing": "Communication Services",
    "entertainment": "Communication Services",
    # Consumer Discretionary
    "automobiles": "Consumer Discretionary",
    "retail": "Consumer Discretionary",
    "hotels restaurants & leisure": "Consumer Discretionary",
    "textiles apparel & luxury goods": "Consumer Discretionary",
    "homebuilding": "Consumer Discretionary",
    "leisure products": "Consumer Discretionary",
    "distributors": "Consumer Discretionary",
    "education": "Consumer Discretionary",
    # Consumer Staples
    "beverages": "Consumer Staples",
    "food products": "Consumer Staples",
    "tobacco": "Consumer Staples",
    "consumer products": "Consumer Staples",
    "household products": "Consumer Staples",
    "personal products": "Consumer Staples",
    # Energy
    "energy": "Energy",
    "oil & gas": "Energy",
    # Financials
    "banking": "Financials",
    "insurance": "Financials",
    "financial services": "Financials",
    "diversified financial services": "Financials",
    "capital markets": "Financials",
    # Health Care
    "health care": "Health Care",
    "pharmaceuticals": "Health Care",
    "biotechnology": "Health Care",
    "life sciences tools & services": "Health Care",
    "medical devices": "Health Care",
    # Industrials
    "aerospace & defense": "Industrials",
    "machinery": "Industrials",
    "industrial conglomerates": "Industrials",
    "building": "Industrials",
    "construction": "Industrials",
    "commercial services & supplies": "Industrials",
    "logistics & transportation": "Industrials",
    "road & rail": "Industrials",
    "airlines": "Industrials",
    "marine": "Industrials",
    "transportation infrastructure": "Industrials",
    "trading companies & distributors": "Industrials",
    "professional services": "Industrials",
    "electrical equipment": "Industrials",
    "business services": "Industrials",
    # Materials
    "chemicals": "Materials",
    "metals & mining": "Materials",
    "packaging": "Materials",
    "paper & forest": "Materials",
    "constr. mat.": "Materials",
    # Real Estate
    "real estate": "Real Estate",
    "reit": "Real Estate",
    # Utilities
    "utilities": "Utilities",
    "electric utilities": "Utilities",
    "gas utilities": "Utilities",
    "water utilities": "Utilities",
}


def industry_to_sector(industry):
    """Roll a Finnhub industry label up to a GICS-style sector, or None."""
    key = str(industry or "").strip().lower()
    if not key:
        return None
    if key in _SECTOR_BY_INDUSTRY:
        return _SECTOR_BY_INDUSTRY[key]
    for needle, sector in _SECTOR_BY_INDUSTRY.items():
        if needle in key or key in needle:
            return sector
    return None


# ============================================================
#  get_info — replaces yfinance Ticker.info (the rate-limit culprit)
# ============================================================
def get_info(ticker):
    """Return a yfinance-shaped 'info' dict built from Finnhub.
    Returns {} on total failure (so renderer code that does info.get('x') still works)."""
    ticker = (ticker or "").upper().strip()
    profile = _get("/stock/profile2", {"symbol": ticker}) or {}
    quote   = _get("/quote",          {"symbol": ticker}) or {}
    raw     = _get("/stock/metric",   {"symbol": ticker, "metric": "all"}) or {}
    m       = raw.get("metric") or {}

    # If all three failed, give up cleanly.
    if not (profile or quote or m):
        return {}

    info = {}

    # ---- identity ----
    info["symbol"]              = ticker
    info["longName"]            = profile.get("name")
    info["shortName"]           = profile.get("name")
    info["exchange"]            = profile.get("exchange")
    info["fullExchangeName"]    = profile.get("exchange")
    info["currency"]            = profile.get("currency")
    info["country"]             = profile.get("country")
    info["website"]             = profile.get("weburl")
    info["industry"]            = profile.get("finnhubIndustry")
    info["industryDisp"]        = profile.get("finnhubIndustry")
    info["industryKey"]         = profile.get("finnhubIndustry")
    info["sector"]              = industry_to_sector(profile.get("finnhubIndustry"))
    info["longBusinessSummary"] = None  # free tier doesn't include a description; renderer shows "—"

    # ---- market cap & shares (Finnhub returns these in millions) ----
    mcap_m = _f(profile.get("marketCapitalization"))
    info["marketCap"] = mcap_m * 1e6 if mcap_m is not None else None
    so_m = _f(profile.get("shareOutstanding"))
    info["sharesOutstanding"] = so_m * 1e6 if so_m is not None else None

    # ---- live price (from /quote) ----
    info["currentPrice"]                = _f(quote.get("c"))
    info["regularMarketPrice"]          = _f(quote.get("c"))
    info["previousClose"]               = _f(quote.get("pc"))
    info["regularMarketPreviousClose"]  = _f(quote.get("pc"))
    info["dayHigh"]                     = _f(quote.get("h"))
    info["dayLow"]                      = _f(quote.get("l"))
    info["regularMarketOpen"]           = _f(quote.get("o"))

    # ---- analyst consensus (drives the Analysts pillar) ----
    # yfinance supplied recommendationMean/Key directly; Finnhub gives raw
    # buy/hold/sell counts, so derive the same numbers from the latest period.
    rec_rows = _get("/stock/recommendation", {"symbol": ticker})
    if rec_rows and isinstance(rec_rows, list):
        r0 = rec_rows[0] or {}
        counts = [int(r0.get(k) or 0) for k in
                  ("strongBuy", "buy", "hold", "sell", "strongSell")]
        total = sum(counts)
        if total > 0:
            mean = sum(c * w for c, w in zip(counts, (1, 2, 3, 4, 5))) / total
            info["recommendationMean"] = mean
            info["numberOfAnalystOpinions"] = total
            info["recommendationKey"] = ("strong_buy" if mean <= 1.5 else
                                         "buy" if mean <= 2.5 else
                                         "hold" if mean <= 3.5 else
                                         "underperform" if mean <= 4.5 else "sell")

    # ---- metrics: direct pass-through ----
    for yk, fk in _METRIC_DIRECT.items():
        info[yk] = _f(m.get(fk))

    # ---- metrics: percent → decimal ----
    for yk, fk in _METRIC_PCT.items():
        info[yk] = _pct_to_dec(m.get(fk))

    # ---- debt/equity: Finnhub returns a plain ratio (1.35); the renderer and its
    # peer comparison expect yfinance's scale (135.5). Multiply by 100. ----
    de = _f(m.get("totalDebt/totalEquityQuarterly"))
    if de is None:
        de = _f(m.get("totalDebt/totalEquityAnnual"))
    info["debtToEquity"] = de * 100.0 if de is not None else None

    # ---- totals derived from per-share metrics x shares outstanding ----
    # Finnhub's free tier has no income statement, but it does publish these
    # per-share figures, and we already know the share count.
    shares = _f(info.get("sharesOutstanding"))
    if shares:
        for key, metric_key in (("totalRevenue",   "revenuePerShareTTM"),
                                ("ebitda",         "ebitdPerShareTTM"),
                                ("freeCashflow",   "cashFlowPerShareTTM")):
            per_share = _f(m.get(metric_key))
            info[key] = per_share * shares if per_share is not None else None

    # ---- enterprise value (Finnhub reports it in millions) ----
    ev = _f(m.get("enterpriseValue"))
    info["enterpriseValue"] = ev * 1e6 if ev is not None else None

    # ---- forward EPS, implied by price / forward P/E ----
    fpe = _f(m.get("forwardPE"))
    px = _f(info.get("currentPrice"))
    info["forwardEps"] = (px / fpe) if (fpe and px) else None

    # ---- headcount: revenue / revenue-per-employee ----
    rev_per_emp = _f(m.get("revenueEmployeeTTM"))     # millions of revenue per employee
    rev = _f(info.get("totalRevenue"))
    if rev_per_emp and rev:
        info["fullTimeEmployees"] = int(round(rev / (rev_per_emp * 1e6)))
    # Revenue generated per employee: a real efficiency signal, and a far more
    # useful sixth tile than a CEO name.
    info["revenuePerEmployee"] = rev_per_emp * 1e6 if rev_per_emp else None

    # ---- yfinance keys with no clean Finnhub equivalent: keep them as None
    # so renderer .get() calls all work and just show "—" in those spots.
    for k in ("fiveYearAvgDividendYield", "trailingAnnualDividendRate",
              "trailingAnnualDividendYield", "totalDebt", "totalCash",
              "operatingCashflow"):
        info.setdefault(k, None)

    return info


# ============================================================
#  get_recommendations — replaces yfinance Ticker.recommendations
# ============================================================
def get_recommendations(ticker):
    """Return a Table matching yfinance .recommendations shape.
    Columns: period, strongBuy, buy, hold, sell, strongSell."""
    rows = _get("/stock/recommendation", {"symbol": (ticker or "").upper().strip()})
    if not rows or not isinstance(rows, list):
        return None
    # Finnhub: newest first. yfinance: oldest first. Reverse so downstream code is consistent.
    rows = list(reversed(rows))
    df = Table([{
        "period":     r.get("period", ""),
        "strongBuy":  int(r.get("strongBuy")  or 0),
        "buy":        int(r.get("buy")        or 0),
        "hold":       int(r.get("hold")       or 0),
        "sell":       int(r.get("sell")       or 0),
        "strongSell": int(r.get("strongSell") or 0),
    } for r in rows])
    return df if not df.empty else None


# ============================================================
#  get_insider_transactions — replaces yfinance Ticker.insider_transactions
# ============================================================
def get_insider_transactions(ticker):
    """Return a Table matching yfinance .insider_transactions shape."""
    resp = _get("/stock/insider-transactions", {"symbol": (ticker or "").upper().strip()})
    if not resp or not isinstance(resp, dict):
        return None
    data = resp.get("data") or []
    if not data:
        return None
    rows = []
    for r in data:
        shares = _f(r.get("share"))
        price  = _f(r.get("transactionPrice"))
        value  = shares * price if (shares is not None and price is not None) else None
        rows.append({
            "Insider":      r.get("name"),
            "Shares":       shares,
            "Value":        value,
            "Start Date":   r.get("transactionDate"),
            "Transaction":  r.get("transactionCode"),
            "Position":     None,  # Finnhub doesn't provide a clean title field
        })
    df = Table(rows)
    return df if not df.empty else None


# ============================================================
#  get_peer_data — replaces fetch_peers() in tickerbase.py
# ============================================================
_PEER_METRICS = ["grossMargins", "operatingMargins", "profitMargins",
                 "returnOnEquity", "returnOnAssets", "trailingPE",
                 "priceToBook", "priceToSalesTrailing12Months",
                 "enterpriseToEbitda", "revenueGrowth", "earningsGrowth",
                 "debtToEquity", "currentRatio"]


def _peer_metric_snapshot(ticker):
    """Pull just the comparable metrics for one peer ticker."""
    raw = _get("/stock/metric", {"symbol": ticker, "metric": "all"})
    if not raw:
        return None
    m = raw.get("metric") or {}
    out = {}
    for yk, fk in _METRIC_DIRECT.items():
        if yk in _PEER_METRICS:
            out[yk] = _f(m.get(fk))
    for yk, fk in _METRIC_PCT.items():
        if yk in _PEER_METRICS:
            out[yk] = _pct_to_dec(m.get(fk))
    return out


def get_peer_data(info):
    """Return {'name','medians','n','tickers'} — same shape as tickerbase.fetch_peers()."""
    if not isinstance(info, dict):
        return None
    self_sym = (info.get("symbol") or "").upper()
    industry = info.get("industry") or ""
    if not self_sym:
        return None

    peer_resp = _get("/stock/peers", {"symbol": self_sym})
    if not peer_resp or not isinstance(peer_resp, list):
        return None
    peers = [str(p).upper() for p in peer_resp if str(p).upper() != self_sym][:9]
    if not peers:
        return None

    vals = {k: [] for k in _PEER_METRICS}
    # Sequential on purpose: Workers/Pyodide has no real threads.
    # Capped at 6 peers so one report stays well inside the CPU budget.
    for p in peers[:6]:
        r = _peer_metric_snapshot(p)
        if not r:
            continue
        for k in _PEER_METRICS:
            x = _f(r.get(k))
            if x is not None:
                vals[k].append(x)

    medians = {}
    for k, lst in vals.items():
        if len(lst) >= 3:                       # need >=3 peers for a meaningful median
            s = sorted(lst)
            n = len(s)
            medians[k] = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

    if not medians:
        return None

    return {"name": industry, "medians": medians,
            "n": max(len(v) for v in vals.values()), "tickers": peers}


# ============================================================
#  get_listed_symbols - the universe of tradable US tickers
# ============================================================
def get_listed_symbols():
    """Every currently listed US symbol, as a set. Returns None on failure so
    callers can tell 'lookup failed' apart from 'nothing is listed'.

    One call covers the whole market, which is why the result is cached for a
    day rather than fetched per visitor."""
    rows = _get("/stock/symbol", {"exchange": "US"})
    if not rows or not isinstance(rows, list):
        return None
    out = set()
    for r in rows:
        sym = (r or {}).get("symbol")
        if sym:
            out.add(str(sym).upper())
    return out or None
