"""
SEC EDGAR fetching for Audit View (network only; the parsing lives in audit_data.py).

SEC rules we follow:
  - every request sends a User-Agent with an app name and contact email
    (from the SEC_USER_AGENT secret, never hardcoded)
  - at most 10 requests/second: we go one at a time with a small gap

Cloudflare Free plan rules we follow:
  - at most 50 outbound requests per incoming request: we count them and stop
    well short (a full load uses about 15-19)
  - CPU time is limited, but waiting on the network is free. The small JSON
    files from the companyconcept API keep parsing cheap.

The HTTP function is passed in, so the same code runs on the Worker (pyfetch)
and on a laptop (urllib, see scripts/sec_local.py).
"""

import asyncio
import json
import time

import audit_data as ad

DATA = "https://data.sec.gov"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
MIN_INTERVAL = 0.12      # seconds between SEC requests (~8/sec max, SEC allows 10)
SUBREQUEST_BUDGET = 45   # Cloudflare Free allows 50; keep headroom


class BudgetExceeded(Exception):
    pass


def worker_http(user_agent):
    """HTTP getter for the Cloudflare Worker. Returns (status, text_or_None)."""
    async def get(url):
        from pyodide.http import pyfetch   # only exists inside the Worker
        r = await pyfetch(url, headers={"User-Agent": user_agent})
        if r.status != 200:
            return r.status, None
        return r.status, await r.string()
    return get


class Edgar:
    def __init__(self, http_get, min_interval=MIN_INTERVAL, budget=SUBREQUEST_BUDGET):
        self._get = http_get
        self._min_interval = min_interval
        self._budget = budget
        self._last = 0.0
        self.requests = 0

    async def get(self, url):
        if self.requests >= self._budget:
            raise BudgetExceeded(f"stopped after {self.requests} SEC requests")
        wait = self._min_interval - (time.time() - self._last)
        if wait > 0:
            await asyncio.sleep(wait)
        self.requests += 1
        try:
            status, text = await self._get(url)
        finally:
            self._last = time.time()
        return status, text

    async def get_json(self, url):
        status, text = await self.get(url)
        if status != 200 or not text:
            return None          # 404 = company doesn't use this tag
        return json.loads(text)

    # ---- endpoints ----
    async def concept(self, cik, tag):
        return await self.get_json(
            f"{DATA}/api/xbrl/companyconcept/CIK{ad.zero_pad_cik(cik)}/us-gaap/{tag}.json")

    async def submissions(self, cik):
        return await self.get_json(f"{DATA}/submissions/CIK{ad.zero_pad_cik(cik)}.json")

    async def archive_file(self, cik, accession, filename):
        url = f"{ARCHIVES}/{int(cik)}/{accession.replace('-', '')}/{filename}"
        status, text = await self.get(url)
        return text if status == 200 else None


# ============================================================
#  High-level loaders
# ============================================================
async def load_concepts(edgar, cik):
    """Fetch the XBRL data for every line item.

    Revenue: all candidate tags (we need to see which is most current).
    Everything else: tags in order, stopping at the first one that has a value
    for the latest fiscal year.
    Returns ({item_key: [(tag, json_or_None), ...]}, entity_name).
    """
    concepts, entity = {}, None

    rev = []
    for tag in ad.ITEM_BY_KEY["revenue"][3]:
        j = await edgar.concept(cik, tag)
        rev.append((tag, j))
        entity = entity or (j or {}).get("entityName")
    concepts["revenue"] = rev

    rev_tag, rev_facts = ad.choose_tag([(t, ad.annual_facts(j, "flow")) for t, j in rev])
    if not rev_facts:
        return concepts, entity
    anchor = ad.fiscal_year_ends(rev_facts)[0]

    for key, _label, kind, tags in ad.LINE_ITEMS:
        if key == "revenue":
            continue
        got = []
        for tag in tags:
            j = await edgar.concept(cik, tag)
            got.append((tag, j))
            entity = entity or (j or {}).get("entityName")
            if ad.covers(ad.annual_facts(j, kind), anchor):
                break
        concepts[key] = got
    return concepts, entity


async def load_financials(edgar, cik):
    """Everything F1-F3 need: the 3-year dataset plus company profile."""
    subs = await edgar.submissions(cik) or {}
    profile = ad.company_profile(subs)
    concepts, entity = await load_concepts(edgar, cik)
    ds = ad.build_dataset(concepts, entity_name=profile["name"] or entity, cik=cik)
    ds["profile"] = profile
    ds["latest_10k"] = ad.latest_10k(subs)
    ds["sec_requests"] = edgar.requests
    return ds


async def load_policy_text(edgar, cik, submissions=None):
    """The revenue recognition text from the latest 10-K, for the AI summary.

    Returns {ok, accession, report_date, text, source, pages} or {ok: False, reason}.
    """
    subs = submissions or await edgar.submissions(cik) or {}
    tenk = ad.latest_10k(subs)
    if not tenk:
        return {"ok": False, "reason": "no_10k"}
    acc = tenk["accession"]
    fs = await edgar.archive_file(cik, acc, "FilingSummary.xml")
    if not fs:
        return {"ok": False, "reason": "no_filing_summary", "accession": acc}
    pages = ad.pick_revenue_pages(ad.parse_filing_summary(fs))
    pol_html = await edgar.archive_file(cik, acc, pages["policies"]) if pages["policies"] else None
    note_html = await edgar.archive_file(cik, acc, pages["note"]) if pages["note"] else None
    text, source = ad.build_policy_text(pol_html, note_html)
    if not text:
        return {"ok": False, "reason": "no_policy_text", "accession": acc}
    return {"ok": True, "accession": acc, "report_date": tenk["report_date"],
            "text": text, "source": source, "pages": pages}
