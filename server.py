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
import edgar_client
import audit_analytics
import audit_ai
import audit_view
from sec_tickers import TICKER_TO_CIK
from landing import LANDING_PAGE

app = FastAPI(title="Tickerbase")

CACHE_TTL = 15 * 60          # report cache, seconds
SYMBOLS_TTL = 24 * 60 * 60   # ticker-validity cache, seconds

# Bump this whenever the report's shape or data mapping changes. It is part of
# the cache key, so old cached reports are ignored instead of being served by a
# newly deployed Worker. Deploys do not clear KV on their own.
CACHE_VERSION = "v7"


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


# ============================================================
#  Audit View (SEC EDGAR)
# ============================================================
# Two separate versions. Bump AUDIT_DATA_VERSION when the SEC data mapping
# changes (tags, alignment); bump AUDIT_AI_VERSION only when the AI prompt or
# model changes, since AI summaries cost neurons to regenerate.
AUDIT_DATA_VERSION = "v3"
AUDIT_AI_VERSION = "v1"
AUDIT_DATA_TTL = 24 * 60 * 60   # SEC data changes at most a few times a year


async def kv_get_json(request, key):
    env = _env(request)
    if env is None:
        return None
    try:
        raw = await env.CACHE.get(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


async def kv_put_json(request, key, value, ttl=None):
    env = _env(request)
    if env is None:
        return
    try:
        if ttl:
            await env.CACHE.put(key, json.dumps(value), expirationTtl=ttl)
        else:
            await env.CACHE.put(key, json.dumps(value))
    except Exception:
        pass


def lookup_cik(ticker):
    t = (ticker or "").strip().upper().replace(".", "-")
    return t, TICKER_TO_CIK.get(t)


def sec_edgar(request):
    env = _env(request)
    ua = str(getattr(env, "SEC_USER_AGENT", "") or "") if env is not None else ""
    if not ua:
        return None
    return edgar_client.Edgar(edgar_client.worker_http(ua))


async def get_audit_financials(request, cik, fresh=False):
    """3-year SEC dataset for one company, cached for a day in KV.
    One KV write per company per day keeps us far inside the free tier."""
    key = f"audit:{AUDIT_DATA_VERSION}:fin:{cik}"
    if not fresh:
        cached = await kv_get_json(request, key)
        if cached is not None:
            cached["cached"] = True
            return cached
    edgar = sec_edgar(request)
    if edgar is None:
        return {"ok": False, "reason": "sec_user_agent_missing"}
    try:
        ds = await edgar_client.load_financials(edgar, cik)
    except Exception as e:  # noqa: BLE001  never crash the page
        return {"ok": False, "reason": "sec_fetch_failed", "detail": str(e)[:200]}
    if ds.get("ok"):
        await kv_put_json(request, key, ds, ttl=AUDIT_DATA_TTL)
    ds["cached"] = False
    return ds


@app.get("/audit/data")
async def audit_data(request: Request, ticker: str = "", fresh: int = 0):
    """Raw Audit View dataset (JSON). Used for checking the data layer."""
    t, cik = lookup_cik(ticker)
    if not cik:
        return JSONResponse({"ok": False, "ticker": t, "reason": "unknown_ticker"})
    ds = await get_audit_financials(request, cik, fresh=bool(fresh) and _debug_enabled(request))
    return JSONResponse({"ticker": t, **ds})


@app.get("/audit")
async def audit(request: Request, ticker: str = ""):
    """Audit View tab: materiality, red flags, fluctuation table (HTML).
    The AI summary loads separately from /audit/summary so this never waits on it."""
    t, cik = lookup_cik(ticker)
    if not t:
        return JSONResponse({"ok": False, "error": "Please enter a ticker symbol."})
    if not cik:
        return JSONResponse({"ok": True, "ticker": t, "has_summary": False,
                             "html": audit_view.render_message(audit_view.NO_XBRL_MESSAGE)})
    fin = await get_audit_financials(request, cik)
    if not fin.get("ok") and fin.get("reason") not in ("no_xbrl_revenue",):
        return JSONResponse({"ok": True, "ticker": t, "has_summary": False,
                             "html": audit_view.render_message(
                                 "Couldn't reach SEC EDGAR just now. Please try again in a minute.")})
    try:
        out = audit_analytics.analyze(fin)
        html = audit_view.render_audit_view(t, out)
    except Exception:  # noqa: BLE001  never crash the page
        return JSONResponse({"ok": True, "ticker": t, "has_summary": False,
                             "html": audit_view.render_message("Audit View couldn't be built for this company.")})
    return JSONResponse({"ok": True, "ticker": t, "html": html,
                         "has_summary": bool(out.get("ok") and fin.get("latest_10k"))})


AI_FAIL_TTL = 60 * 60   # after a failed summary, wait an hour before trying again


def _debug_enabled(request):
    """True only on preview versions uploaded with --var AUDIT_DEBUG:1."""
    env = _env(request)
    return env is not None and str(getattr(env, "AUDIT_DEBUG", "") or "") == "1"


@app.get("/audit/summary")
async def audit_summary(request: Request, ticker: str = "", model: str = "", fresh: int = 0):
    """F4: AI summary of the latest 10-K's revenue recognition policy.

    Cached permanently in KV by 10-K accession number: a filed 10-K never
    changes, so we never pay for the same summary twice."""
    t, cik = lookup_cik(ticker)
    try:
        payload = await _audit_summary(request, t, cik, model, fresh)
    except Exception:  # noqa: BLE001  never crash the page
        payload = {"ok": False, "reason": "summary_unavailable"}
    payload["ticker"] = t
    payload["html"] = audit_view.render_summary(payload)
    return JSONResponse(payload)


async def _audit_summary(request, t, cik, model, fresh):
    if not cik:
        return {"ok": False, "reason": "unknown_ticker"}
    debug = _debug_enabled(request)
    use_model = model if (debug and model) else audit_ai.MODEL

    fin = await get_audit_financials(request, cik)
    tenk = fin.get("latest_10k") if fin.get("ok") else None
    if not tenk:
        return {"ok": False, "reason": fin.get("reason") or "no_10k"}
    acc = tenk["accession"]
    key = f"audit:{AUDIT_AI_VERSION}:ai:{acc}"
    fail_key = f"audit:{AUDIT_AI_VERSION}:aifail:{acc}"
    if debug and model:
        key += ":" + model
        fail_key += ":" + model

    if not (debug and fresh):
        cached = await kv_get_json(request, key)
        if cached is not None:
            return {**cached, "cached": True}
        if await kv_get_json(request, fail_key):
            return {"ok": False, "reason": "summary_unavailable"}

    env = _env(request)
    edgar = sec_edgar(request)
    if edgar is None or env is None or getattr(env, "AI", None) is None:
        return {"ok": False, "reason": "not_configured"}
    try:
        pol = await edgar_client.load_policy_text(edgar, cik)
    except Exception as e:  # noqa: BLE001
        pol = {"ok": False, "reason": f"sec_fetch_failed: {str(e)[:120]}"}
    if not pol.get("ok"):
        await kv_put_json(request, fail_key, {"reason": pol.get("reason")}, ttl=AI_FAIL_TTL)
        return {"ok": False, "reason": pol.get("reason")}

    analysis = audit_analytics.analyze(fin)
    _lines, valid_flags = audit_ai.flag_lines(analysis)
    messages = audit_ai.build_messages(fin.get("entity"), (fin.get("fy_ends") or [""])[0],
                                       pol["text"], analysis)
    result = await audit_ai.summarize(audit_ai.worker_runner(env.AI), use_model, messages,
                                      pol["text"], valid_flags)
    payload = {"ok": result["ok"], "accession": acc, "report_date": pol.get("report_date"),
               "source": pol.get("source"), "model": use_model, "label": audit_ai.PAGE_LABEL,
               "attempts": result["attempts"]}
    if result["ok"]:
        payload["summary"] = result["summary"]
        await kv_put_json(request, key, payload)              # permanent
    else:
        payload["reason"] = "summary_unavailable"
        if debug:
            payload["detail"] = result.get("reason")
        await kv_put_json(request, fail_key, {"reason": result.get("reason")}, ttl=AI_FAIL_TTL)
    return {**payload, "cached": False}




