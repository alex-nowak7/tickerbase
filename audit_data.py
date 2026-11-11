"""
Audit View - turning raw SEC EDGAR responses into clean 3-year data.

Everything in this file is a PURE function: it takes data in and returns data
out. No network calls and no Cloudflare APIs, so it can be unit-tested on a
laptop with saved SEC responses (tests/fixtures/).

Background, in plain English:
  - XBRL is the tagging system public companies use in their SEC filings. Every
    number in a 10-K carries a tag such as "NetIncomeLoss", so software can read
    it without scraping the PDF.
  - SEC's companyconcept API returns every value a company has ever filed for a
    single tag: annual, quarterly, and each prior year repeated as a comparative
    figure in later filings. Our job here is to pick out the 3 latest fiscal
    years and the right value for each.
"""

import html as _html
import re
from datetime import date

# ============================================================
#  Config
# ============================================================
ANNUAL_FORMS = ("10-K", "10-K/A")
FLOW_MIN_DAYS, FLOW_MAX_DAYS = 350, 380   # a "full year" period (52/53-week years included)
YEARS = 3
MATCH_TOLERANCE_DAYS = 7                  # line items whose end date is a few days off still match
RESTATEMENT_TOLERANCE = 0.01              # >1% difference between filings = changed figure (F3-f)
POLICY_TEXT_CAP = 12_000                  # characters sent to the AI summary

# Banks and insurers present their statements differently (no "revenue" or
# "cost of sales" in the usual sense), so Audit View only offers limited support.
# SIC = Standard Industrial Classification code the SEC assigns each company.
FINANCIAL_SIC_RANGES = ((6000, 6199), (6300, 6411))

# (key, label, kind, tags to try in order). "flow" = measured over a year
# (income statement, cash flow). "instant" = a balance on one date (balance sheet).
LINE_ITEMS = [
    ("revenue", "Revenue", "flow",
     ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"]),
    ("cost_of_revenue", "Cost of revenue", "flow",
     ["CostOfRevenue", "CostOfGoodsAndServicesSold"]),
    ("gross_profit", "Gross profit", "flow", ["GrossProfit"]),
    ("sga", "SG&A", "flow", ["SellingGeneralAndAdministrativeExpense"]),
    ("operating_income", "Operating income", "flow", ["OperatingIncomeLoss"]),
    ("pretax_income", "Pre-tax income", "flow",
     ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
      "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"]),
    # ProfitLoss is not in the original spec; Caterpillar stopped tagging
    # NetIncomeLoss annually after 2010. It is consolidated profit, which pairs
    # with consolidated operating cash flow in test F3-d.
    ("net_income", "Net income", "flow", ["NetIncomeLoss", "ProfitLoss"]),
    ("operating_cash_flow", "Operating cash flow", "flow",
     ["NetCashProvidedByUsedInOperatingActivities"]),
    # ReceivablesNetCurrent is not in the original spec; Walmart uses it.
    ("accounts_receivable", "Accounts receivable", "instant",
     ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"]),
    ("inventory", "Inventory", "instant", ["InventoryNet"]),
    ("deferred_revenue", "Deferred revenue", "instant",
     ["ContractWithCustomerLiabilityCurrent", "DeferredRevenueCurrent"]),
    ("total_assets", "Total assets", "instant", ["Assets"]),
]
ITEM_BY_KEY = {k: (k, label, kind, tags) for k, label, kind, tags in LINE_ITEMS}


# ============================================================
#  Small helpers
# ============================================================
def _d(s):
    return date.fromisoformat(s)


def _days(a, b):
    return abs((_d(a) - _d(b)).days)


def zero_pad_cik(cik):
    """data.sec.gov URLs need the CIK padded to 10 digits: 320193 -> 0000320193."""
    return f"{int(cik):010d}"


# ============================================================
#  Step 1: keep only annual 10-K facts
# ============================================================
def annual_facts(concept_json, kind):
    """From one companyconcept response, keep the facts that come from a 10-K
    (or amended 10-K) and cover a full year (flow) or a single date (instant).

    Returns a list of dicts: {end, val, filed, accn, form}.
    """
    if not concept_json:
        return []
    facts = (concept_json.get("units") or {}).get("USD") or []
    out = []
    for f in facts:
        if f.get("form") not in ANNUAL_FORMS or f.get("val") is None or not f.get("end"):
            continue
        start = f.get("start")
        if kind == "flow":
            if not start:
                continue
            length = (_d(f["end"]) - _d(start)).days
            if not (FLOW_MIN_DAYS <= length <= FLOW_MAX_DAYS):
                continue
        elif start:            # instant items have no start date
            continue
        out.append({"end": f["end"], "val": f["val"], "filed": f.get("filed", ""),
                    "accn": f.get("accn", ""), "form": f.get("form")})
    return out


def latest_end(facts):
    return max((f["end"] for f in facts), default=None)


# ============================================================
#  Step 2: decide which fiscal years we analyze, and which tag to trust
# ============================================================
def fiscal_year_ends(revenue_facts, years=YEARS):
    """The 3 most recent fiscal year-end dates, newest first, taken from revenue.

    We line everything up by END DATE, not by the "fy" field, because "fy" is the
    fiscal year of the filing a number came from. A 2026 10-K also reports 2025
    and 2024 figures, all stamped fy=2026.
    """
    ends = sorted({f["end"] for f in revenue_facts}, reverse=True)
    picked = []
    for e in ends:
        # skip near-duplicates (e.g. a 53-week year end a few days off)
        if all(_days(e, p) > 300 for p in picked):
            picked.append(e)
        if len(picked) == years:
            break
    return picked


def covers(facts, anchor_end):
    """True if this tag has a value for the latest fiscal year."""
    le = latest_end(facts)
    if le is None:
        return False
    return (_d(anchor_end) - _d(le)).days <= MATCH_TOLERANCE_DAYS


def choose_tag(candidates, anchor_end=None):
    """Pick which tag's data to use for one line item.

    candidates: list of (tag, facts) in the spec's preferred order.

    Companies switch tags over time. Apple used "SalesRevenueNet" until 2017,
    then moved to "RevenueFromContractWithCustomerExcludingAssessedTax" when the
    new revenue standard (ASC 606) arrived. Using "the first tag that has any
    data" could pick a tag that stopped in 2017, so instead:
      - with an anchor (latest fiscal year-end): first tag, in order, that has
        a value for that year; if none does, the tag with the most recent data.
      - without an anchor (revenue itself): the tag with the most recent data;
        ties go to the earlier tag in the list.
    Returns (tag, facts) or (None, []).
    """
    usable = [(t, f) for t, f in candidates if f]
    if not usable:
        return None, []
    if anchor_end:
        for t, f in usable:
            if covers(f, anchor_end):
                return t, f
    best = max(usable, key=lambda tf: latest_end(tf[1]))   # max() keeps the first on ties
    return best


# ============================================================
#  Step 3: one value per fiscal year, and spot changed prior-year figures
# ============================================================
def resolve_values(facts, fy_ends):
    """For each fiscal year-end, return the value from the MOST RECENT filing.

    A year's number usually appears in 2-3 different 10-Ks (as the current year,
    then as a comparative). If an older filing showed a different figure (>1%),
    that is a restatement or reclassification. We keep it for red-flag test F3-f.

    Returns (values, changes):
      values  - list aligned with fy_ends, None where not reported
      changes - list of {end, current, original, current_filed, original_filed, pct}
    """
    values, changes = [], []
    for fy in fy_ends:
        matches = [f for f in facts if _days(f["end"], fy) <= MATCH_TOLERANCE_DAYS]
        if not matches:
            values.append(None)
            continue
        exact = [f for f in matches if f["end"] == fy]
        pool = exact or matches
        current = max(pool, key=lambda f: (f["filed"], f["accn"]))
        values.append(current["val"])

        # One entry per older filing, compared with the current figure.
        seen = set()
        for f in sorted(pool, key=lambda f: f["filed"]):
            if f["accn"] == current["accn"] or f["accn"] in seen:
                continue
            seen.add(f["accn"])
            cur, old = current["val"], f["val"]
            if cur == old:
                continue
            pct = abs(cur - old) / abs(cur) if cur else float("inf")
            if pct > RESTATEMENT_TOLERANCE:
                changes.append({"end": fy, "current": cur, "original": old,
                                "current_filed": current["filed"],
                                "original_filed": f["filed"],
                                "pct": None if pct == float("inf") else round(pct, 4)})
    return values, changes


# ============================================================
#  Step 4: put it all together
# ============================================================
def build_dataset(concepts, entity_name=None, cik=None):
    """concepts: {item_key: [(tag, concept_json_or_None), ...]} in tag order.

    Returns a JSON-friendly dict:
      {ok, entity, cik, fy_ends, items: {key: {label, kind, tag, values, derived}},
       restatements: [...], missing: [...]}
    ok=False (with 'reason') when the company has no usable annual XBRL revenue.
    """
    def facts_for(key):
        kind = ITEM_BY_KEY[key][2]
        return [(t, annual_facts(j, kind)) for t, j in concepts.get(key, [])]

    rev_tag, rev_facts = choose_tag(facts_for("revenue"))
    if not rev_facts:
        return {"ok": False, "reason": "no_xbrl_revenue", "entity": entity_name, "cik": cik}
    fy_ends = fiscal_year_ends(rev_facts)
    anchor = fy_ends[0]

    items, restatements, missing = {}, [], []
    for key, label, kind, _tags in LINE_ITEMS:
        if key == "revenue":
            tag, facts = rev_tag, rev_facts
        else:
            tag, facts = choose_tag(facts_for(key), anchor)
        values, changes = resolve_values(facts, fy_ends) if facts else ([None] * len(fy_ends), [])
        items[key] = {"label": label, "kind": kind, "tag": tag, "values": values, "derived": False}
        for c in changes:
            restatements.append({"item": key, "label": label, **c})

    # Gross profit: many companies (e.g. Walmart) don't tag it. Revenue minus
    # cost of revenue is the same number by definition.
    gp = items["gross_profit"]
    rev, cor = items["revenue"]["values"], items["cost_of_revenue"]["values"]
    if any(v is None for v in gp["values"]):
        filled = [g if g is not None else (r - c if r is not None and c is not None else None)
                  for g, r, c in zip(gp["values"], rev, cor)]
        if filled != gp["values"]:
            gp["values"] = filled
            gp["derived"] = gp["tag"] is None
            if gp["tag"] is None:
                gp["tag"] = "Revenue - cost of revenue"

    for key, item in items.items():
        if all(v is None for v in item["values"]):
            missing.append(key)

    return {"ok": True, "entity": entity_name, "cik": cik, "fy_ends": fy_ends,
            "items": items, "restatements": restatements, "missing": missing}


# ============================================================
#  Company profile and latest 10-K (from the submissions JSON)
# ============================================================
def company_profile(submissions):
    """Name, industry code, and whether the company is a bank/insurer."""
    sic = submissions.get("sic")
    try:
        sic_n = int(sic)
    except (TypeError, ValueError):
        sic_n = None
    is_fin = sic_n is not None and any(lo <= sic_n <= hi for lo, hi in FINANCIAL_SIC_RANGES)
    return {"name": submissions.get("name"), "sic": sic_n,
            "sic_description": submissions.get("sicDescription"), "is_financial": is_fin}


def latest_10k(submissions):
    """The most recent original 10-K in the company's recent filings, or None
    (e.g. foreign companies that file 20-F instead)."""
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    for i, form in enumerate(forms):
        if form == "10-K":
            return {"accession": recent["accessionNumber"][i],
                    "primary_document": recent["primaryDocument"][i],
                    "report_date": recent.get("reportDate", [None] * len(forms))[i],
                    "filing_date": recent.get("filingDate", [None] * len(forms))[i]}
    return None


# ============================================================
#  Revenue policy text (FilingSummary.xml + R pages)
# ============================================================
def parse_filing_summary(xml):
    """List the report pages in a filing: [{short_name, html_file, category}]."""
    out = []
    for m in re.finditer(r"<Report\b[^>]*>(.*?)</Report>", xml or "", re.S):
        blk = m.group(1)

        def field(name):
            f = re.search(rf"<{name}>(.*?)</{name}>", blk, re.S)
            return _html.unescape(f.group(1).strip()) if f else ""
        if field("HtmlFileName"):
            out.append({"short_name": field("ShortName"), "html_file": field("HtmlFileName"),
                        "category": field("MenuCategory")})
    return out


def pick_revenue_pages(reports):
    """Choose which R pages to read for the revenue policy.

    Returns {"policies": file or None, "note": file or None}.
      policies - the accounting-policies page; we pull just its revenue rows.
      note     - a dedicated revenue note (e.g. Apple's "Revenue"), if one exists.
                 Segment pages that merely mention revenue are skipped.
    """
    def has_rev(r):
        return "revenue" in r["short_name"].lower()

    policies = [r for r in reports if r["category"] == "Policies"]
    rev_policy = [r for r in policies if has_rev(r)]
    sig_policy = [r for r in policies if "significant accounting polic" in r["short_name"].lower()]
    notes = [r for r in reports if r["category"] == "Notes"]
    rev_notes = [r for r in notes if has_rev(r) and "segment" not in r["short_name"].lower()]
    sig_notes = [r for r in notes if "significant accounting polic" in r["short_name"].lower()]

    pol = (rev_policy or sig_policy or policies or sig_notes or [None])[0]
    note = (rev_notes or [None])[0]
    return {"policies": pol["html_file"] if pol else None,
            "note": note["html_file"] if note else None}


def html_to_text(fragment):
    """Strip HTML to readable plain text."""
    t = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", fragment or "")
    t = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>|</h\d>", "\n", t)
    t = re.sub(r"(?i)</td>", " ", t)
    t = _html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"[ \t\xa0​]+", " ", t)
    t = re.sub(r"\s*\n\s*", "\n", t)
    return t.strip()


def _report_rows(r_html):
    """Split an R page into (label, element, content_html) rows. The page ends
    with hidden XBRL definition tables ("authRefData"); we cut those off."""
    body = (r_html or "").split('class="authRefData"')[0]
    starts = [m.start() for m in re.finditer(r'<tr class="r[eo]"', body)]
    rows = []
    for i, s in enumerate(starts):
        chunk = body[s: starts[i + 1] if i + 1 < len(starts) else len(body)]
        pl = re.search(r'<td class="pl"[^>]*>(.*?)</td>', chunk, re.S)
        el = re.search(r"defref_([\w-]+)", chunk)
        content = chunk[pl.end():] if pl else chunk
        rows.append((html_to_text(pl.group(1)) if pl else "",
                     el.group(1) if el else "", content))
    return rows


def revenue_policy_rows(r_html):
    """Text of just the revenue-related rows of an accounting-policies page."""
    parts = []
    for label, element, content in _report_rows(r_html):
        if "revenue" in label.lower() or "Revenue" in element:
            txt = html_to_text(content)
            if txt:
                parts.append(txt)
    return "\n\n".join(parts)


def whole_page_text(r_html):
    """All text rows of an R page (used for a dedicated revenue note)."""
    return "\n\n".join(t for t in (html_to_text(c) for _l, _e, c in _report_rows(r_html)) if t)


def build_policy_text(policies_html, note_html, cap=POLICY_TEXT_CAP):
    """Combine the revenue policy and the revenue note into one capped text.
    Falls back to the whole policies page if no revenue rows are found.
    Returns (text, source) where source says what was used."""
    pieces, sources = [], []
    pol = revenue_policy_rows(policies_html) if policies_html else ""
    if pol:
        pieces.append(pol)
        sources.append("revenue policy")
    if note_html:
        note = whole_page_text(note_html)
        if note:
            pieces.append(note)
            sources.append("revenue note")
    if not pieces and policies_html:
        pieces.append(whole_page_text(policies_html))
        sources.append("full accounting policies (no revenue section found)")
    text = "\n\n".join(pieces)
    if len(text) > cap:
        text = text[:cap].rsplit(" ", 1)[0] + " …"
    return text, ", ".join(sources)
