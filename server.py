"""
Tickerbase - web backend (Cloudflare Workers edition).

Changes from the Render version:
  - no in-memory cache        -> Workers KV (isolates are short-lived)
  - no per-IP rate limiter    -> Cloudflare edge rate limiting rules (free)
  - no threading              -> Pyodide has no real threads
Everything else is identical.
"""

import json

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

import tickerbase as sl
import finnhub_client
from landing import LANDING_PAGE

app = FastAPI(title="Tickerbase")

CACHE_TTL = 15 * 60          # report cache, seconds
SYMBOLS_TTL = 24 * 60 * 60   # ticker-validity cache, seconds

# Bump this whenever the report's shape or data mapping changes. It is part of
# the cache key, so old cached reports are ignored instead of being served by a
# newly deployed Worker. Deploys do not clear KV on their own.
CACHE_VERSION = "v2"


def _env(request):
    """The Worker's env object, stashed by the entrypoint. None when running
    plain uvicorn locally."""
    return getattr(request.app.state, "env", None)


async def cache_get(request, key):
    env = _env(request)
    if env is None:
        return None
    try:
        raw = await env.CACHE.get(f"report:{CACHE_VERSION}:{key}")
        return json.loads(raw) if raw else None
    except Exception:
        return None


async def cache_set(request, key, value):
    env = _env(request)
    if env is None:
        return
    try:
        await env.CACHE.put(f"report:{CACHE_VERSION}:{key}", json.dumps(value),
                            expirationTtl=CACHE_TTL)
    except Exception:
        pass


@app.get("/", response_class=HTMLResponse)
async def home():
    return LANDING_PAGE


@app.get("/healthz", response_class=PlainTextResponse)
async def health():
    return "ok"


@app.get("/validate")
async def validate(request: Request, tickers: str = ""):
    """Return which of the given tickers are still listed in the US.

    The landing page calls this once on load to hide chips for companies that
    have merged, delisted or been renamed, so the category lists stay correct
    without anyone maintaining them.

    Only the small answer is cached, not the ~25,000-symbol universe: reparsing
    that on every request would blow the Worker's CPU budget.
    """
    wanted = [x.strip().upper() for x in (tickers or "").split(",") if x.strip()]
    if not wanted:
        return JSONResponse({"valid": [], "checked": False})

    env = _env(request)
    key = "symok:" + str(abs(hash(",".join(sorted(wanted)))))

    if env is not None:
        try:
            raw = await env.CACHE.get(key)
            if raw:
                return JSONResponse({"valid": json.loads(raw), "checked": True, "cached": True})
        except Exception:
            pass

    universe = finnhub_client.get_listed_symbols()
    if not universe:
        # Lookup failed. Say so rather than hiding every chip.
        return JSONResponse({"valid": wanted, "checked": False})

    valid = [t for t in wanted if t in universe or t.replace("-", ".") in universe]

    if env is not None:
        try:
            await env.CACHE.put(key, json.dumps(valid), expirationTtl=SYMBOLS_TTL)
        except Exception:
            pass
    return JSONResponse({"valid": valid, "checked": True, "cached": False})


@app.get("/debug")
async def debug(request: Request, ticker: str = "AAPL"):
    """Raw field dump. Lets us see exactly what the live Worker computed
    instead of inferring it from the rendered page."""
    import finnhub_client
    info = finnhub_client.get_info((ticker or "AAPL").strip().upper()) or {}
    keys = ("symbol", "longName", "sector", "industry", "currentPrice",
            "forwardPE", "forwardEps", "pegRatio", "enterpriseToEbitda",
            "totalRevenue", "ebitda", "freeCashflow", "fullTimeEmployees",
            "debtToEquity", "trailingPE", "marketCap", "sharesOutstanding")
    return JSONResponse({k: info.get(k) for k in keys})


@app.get("/analyze")
async def analyze(request: Request, ticker: str = "", fresh: int = 0):
    t = (ticker or "").strip().upper()
    if not t:
        return JSONResponse({"ok": False, "error": "Please enter a ticker symbol."},
                            status_code=400)

    if not fresh:
        cached = await cache_get(request, t)
        if cached is not None:
            return JSONResponse({**cached, "cached": True})

    result = sl.generate_report(t)   # never raises; returns a dict
    if result.get("ok"):
        payload = {"ok": True, "ticker": result["ticker"],
                   "html": result["html"], "partial": result.get("partial", False)}
        await cache_set(request, t, payload)
        return JSONResponse({**payload, "cached": False})

    return JSONResponse({"ok": False, "ticker": t,
                         "error": result.get("error", "Unknown error.")},
                        status_code=200)



