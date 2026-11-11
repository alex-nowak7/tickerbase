"""
Audit View - the analytics engine (F1 materiality, F2 fluctuations, F3 red flags).

This is a simplified version of the "planning analytics" an audit team runs
before fieldwork starts: decide what size of error matters (materiality), look
for unusual year-over-year movements (analytical procedures), and run a few
targeted ratio tests that point at where misstatements tend to hide.

Every function here is PURE: data in, data out. Input is the dataset built by
audit_data.build_dataset(); all values are in US dollars, and every list of
values is ordered newest year first: [latest, prior, two years back].
"""

# ============================================================
#  CONFIG - every threshold in one place
# ============================================================
MATERIALITY_PRETAX_PCT = 0.05     # F1: 5% of pre-tax income
MATERIALITY_REVENUE_PCT = 0.005   # F1 fallback: 0.5% of revenue
MIN_PRETAX_TO_REVENUE = 0.01      # F1: use revenue if pre-tax income < 1% of revenue
FLUX_PCT_THRESHOLD = 0.10         # F2: flag if |% change| >= 10% ...
                                  #     ... AND |$ change| >= materiality
AR_GAP_PTS = 10.0                 # F3-a: AR growth - revenue growth > 10 points
INVENTORY_GAP_PTS = 10.0          # F3-b: inventory growth - cost of revenue growth > 10 points
GROSS_MARGIN_SWING_PTS = 2.0      # F3-c: gross margin moves > 2 points either way
ACCRUALS_TO_ASSETS = 0.10         # F3-d: (net income - operating cash flow) / avg assets > 10%
DEFREV_REVENUE_UP = 0.10          # F3-e: revenue up > 10% ...
DEFREV_DOWN = 0.10                #       ... while deferred revenue down > 10%
DAYS_IN_YEAR = 365

FLAG, OK, NO_DATA = "flag", "ok", "not enough data"

MATERIALITY_NOTE = ("Auditors set materiality to decide how big a mistake has to be to matter. "
                    "Real audits use more judgment; this is a simplified rule.")


# ============================================================
#  Helpers
# ============================================================
def _vals(ds, key):
    item = (ds.get("items") or {}).get(key) or {}
    vals = list(item.get("values") or [])
    return vals + [None] * (3 - len(vals))


def growth(cur, prior):
    """Year-over-year growth as a fraction (0.12 = +12%). None when it can't be
    computed honestly: a missing year, or a prior value of zero or below
    (growth from a negative base has no sensible meaning)."""
    if cur is None or prior is None or prior <= 0:
        return None
    return (cur - prior) / prior


def ratio(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


def _pts(x):
    """Fraction -> percentage points, rounded for display."""
    return None if x is None else round(x * 100, 1)


def _result(test_id, name, status, numbers, threshold, note, assertion, why):
    return {"id": test_id, "name": name, "status": status, "numbers": numbers,
            "threshold": threshold, "note": note, "assertion": assertion, "why": why}


# ============================================================
#  F1 - Planning materiality
# ============================================================
def materiality(ds):
    """Materiality is the size of misstatement that would change a reasonable
    reader's decision. Auditors set it at the start so they know which
    balances and differences are big enough to test.

    Rule: 5% of pre-tax income. Pre-tax income is the usual benchmark for
    profitable companies because investors focus on earnings. If the company
    has a loss, or profit is tiny (< 1% of revenue), 5% of it would be
    meaninglessly small, so we use 0.5% of revenue instead.
    """
    pretax = _vals(ds, "pretax_income")[0]
    revenue = _vals(ds, "revenue")[0]
    base = {"note": MATERIALITY_NOTE, "pretax_income": pretax, "revenue": revenue}

    if pretax is not None and pretax > 0 and (revenue is None or pretax >= MIN_PRETAX_TO_REVENUE * revenue):
        return {**base, "status": OK, "amount": pretax * MATERIALITY_PRETAX_PCT,
                "benchmark": "pretax_income", "benchmark_label": "5% of pre-tax income",
                "benchmark_value": pretax, "reason": None}

    if revenue is not None and revenue > 0:
        if pretax is None:
            reason = "Pre-tax income not reported, so revenue is used instead."
        elif pretax <= 0:
            reason = "The company had a pre-tax loss, so revenue is used instead."
        else:
            reason = "Pre-tax income is under 1% of revenue, so revenue is used instead."
        return {**base, "status": OK, "amount": revenue * MATERIALITY_REVENUE_PCT,
                "benchmark": "revenue", "benchmark_label": "0.5% of revenue",
                "benchmark_value": revenue, "reason": reason}

    return {**base, "status": NO_DATA, "amount": None, "benchmark": None,
            "benchmark_label": None, "benchmark_value": None,
            "reason": "Neither pre-tax income nor revenue is available."}


# ============================================================
#  F2 - Fluctuation analysis (analytical procedures)
# ============================================================
# One hand-written note per line item: why an auditor would care if it moved a
# lot, and the assertion (the claim in the financial statements) most at risk.
FLUX_NOTES = {
    "revenue": ("Revenue is the most common place for misstatement. A big swing needs a clear business reason.",
                "Existence"),
    "cost_of_revenue": ("Costs should move with sales. A different pattern can mean costs recorded in the wrong period.",
                        "Completeness"),
    "gross_profit": ("A change in gross profit points to pricing, product mix, or how costs were recorded.",
                     "Accuracy"),
    "sga": ("Unusual swings in overhead can mean unrecorded or misclassified expenses.",
            "Completeness"),
    "operating_income": ("Operating income sums up the swings above. The team needs to know what drove it.",
                         "Accuracy"),
    "pretax_income": ("Pre-tax income is the materiality base, so a big change also changes how much testing is needed.",
                      "Accuracy"),
    "net_income": ("Large swings often come from one-off items that need support.",
                   "Accuracy"),
    "operating_cash_flow": ("Cash flow moving differently from profit is an earnings-quality signal.",
                            "Accuracy"),
    "accounts_receivable": ("Growing receivables can mean sales that never happened or customers who won't pay.",
                            "Existence"),
    "inventory": ("An inventory build-up can hide slow-moving or obsolete stock that should be written down.",
                  "Valuation"),
    "deferred_revenue": ("Deferred revenue shows cash received before it is earned. Big moves can mean revenue recognized too early or too late.",
                         "Cutoff"),
    "total_assets": ("Big balance sheet moves usually come from acquisitions, disposals, or write-downs that need support.",
                     "Valuation"),
}


def compare(cur, prior, materiality_amount):
    """One year-over-year comparison for one line item.

    Analytical procedures: auditors compare this year with last year and ask
    "does this movement make sense?" A movement that is both large in dollars
    (at least materiality) and large in percent (at least 10%) gets flagged for
    follow-up questions.
    """
    if cur is None or prior is None:
        return {"cur": cur, "prior": prior, "change": None, "pct": None, "flag": False,
                "status": NO_DATA}
    change = cur - prior
    pct = change / abs(prior) if prior != 0 else None
    big_dollars = materiality_amount is not None and abs(change) >= materiality_amount
    # From zero to something is an unlimited % change, so it passes the % test.
    big_pct = (abs(pct) >= FLUX_PCT_THRESHOLD) if pct is not None else change != 0
    flag = big_dollars and big_pct
    return {"cur": cur, "prior": prior, "change": change, "pct": pct, "flag": flag,
            "status": FLAG if flag else OK}


def fluctuations(ds, materiality_amount):
    """F2: compare latest vs prior year and prior vs two years back, per line item."""
    rows = []
    for key, item in (ds.get("items") or {}).items():
        v = _vals(ds, key)
        why, assertion = FLUX_NOTES.get(key, ("", ""))
        latest = compare(v[0], v[1], materiality_amount)
        earlier = compare(v[1], v[2], materiality_amount)
        flagged = latest["flag"] or earlier["flag"]
        rows.append({"key": key, "label": item.get("label", key), "values": v,
                     "derived": item.get("derived", False),
                     "latest": latest, "earlier": earlier, "flag": flagged,
                     "why": why if flagged else None,
                     "assertion": assertion if flagged else None})
    return rows


# ============================================================
#  F3 - Red-flag tests (latest year vs prior year)
# ============================================================
def test_receivables_vs_revenue(ds):
    """F3-a. If receivables grow much faster than sales, the company may be
    booking sales it hasn't really made, or selling to customers who can't pay.
    Auditors respond by confirming balances with customers and testing collections."""
    rev, ar = _vals(ds, "revenue"), _vals(ds, "accounts_receivable")
    g_rev, g_ar = growth(rev[0], rev[1]), growth(ar[0], ar[1])
    dso = [ratio(a, r) * DAYS_IN_YEAR if ratio(a, r) is not None else None
           for a, r in ((ar[0], rev[0]), (ar[1], rev[1]))]
    numbers = {"revenue_growth_pct": _pts(g_rev), "ar_growth_pct": _pts(g_ar),
               "dso_latest": None if dso[0] is None else round(dso[0], 1),
               "dso_prior": None if dso[1] is None else round(dso[1], 1)}
    if g_rev is None or g_ar is None:
        status, gap = NO_DATA, None
        note = "Receivables or revenue not reported for both years."
    else:
        gap = (g_ar - g_rev) * 100
        status = FLAG if gap > AR_GAP_PTS else OK
        note = (f"Receivables grew {gap:.1f} points faster than revenue." if status == FLAG else
                f"Receivables grew in line with revenue (gap {gap:+.1f} points).")
    numbers["gap_pts"] = None if gap is None else round(gap, 1)
    return _result("F3a", "Receivables growing faster than revenue", status, numbers,
                   f"Flag if AR growth minus revenue growth > {AR_GAP_PTS:g} points",
                   note, "Existence, valuation",
                   "Revenue may be overstated or not collectible.")


def test_inventory_vs_cost(ds):
    """F3-b. Inventory piling up faster than the company sells it can mean
    products that won't sell at full price. Auditors then test whether
    inventory should be written down to what it can actually be sold for."""
    inv, cor = _vals(ds, "inventory"), _vals(ds, "cost_of_revenue")
    if all(v is None for v in inv):
        return _result("F3b", "Inventory growing faster than cost of sales", NO_DATA,
                       {}, f"Flag if inventory growth minus cost of revenue growth > {INVENTORY_GAP_PTS:g} points",
                       "The company reports no inventory.", "Valuation",
                       "Inventory may be slow-moving or obsolete.")
    g_inv, g_cor = growth(inv[0], inv[1]), growth(cor[0], cor[1])
    dio = [ratio(i, c) * DAYS_IN_YEAR if ratio(i, c) is not None else None
           for i, c in ((inv[0], cor[0]), (inv[1], cor[1]))]
    numbers = {"cost_growth_pct": _pts(g_cor), "inventory_growth_pct": _pts(g_inv),
               "days_inventory_latest": None if dio[0] is None else round(dio[0], 1),
               "days_inventory_prior": None if dio[1] is None else round(dio[1], 1)}
    if g_inv is None or g_cor is None:
        status, gap = NO_DATA, None
        note = "Inventory or cost of revenue not reported for both years."
    else:
        gap = (g_inv - g_cor) * 100
        status = FLAG if gap > INVENTORY_GAP_PTS else OK
        note = (f"Inventory grew {gap:.1f} points faster than cost of sales." if status == FLAG else
                f"Inventory grew in line with cost of sales (gap {gap:+.1f} points).")
    numbers["gap_pts"] = None if gap is None else round(gap, 1)
    return _result("F3b", "Inventory growing faster than cost of sales", status, numbers,
                   f"Flag if inventory growth minus cost of revenue growth > {INVENTORY_GAP_PTS:g} points",
                   note, "Valuation", "Inventory may be slow-moving or obsolete.")


def test_gross_margin_swing(ds):
    """F3-c. Gross margin is usually stable from year to year. A sudden jump or
    drop can mean costs were recorded in the wrong year or put on the wrong line.
    Auditors test cutoff around year-end and how costs are classified."""
    rev, gp = _vals(ds, "revenue"), _vals(ds, "gross_profit")
    gm = [ratio(gp[0], rev[0]), ratio(gp[1], rev[1])]
    numbers = {"gross_margin_latest_pct": _pts(gm[0]), "gross_margin_prior_pct": _pts(gm[1])}
    if gm[0] is None or gm[1] is None:
        status, swing = NO_DATA, None
        note = "Gross profit or revenue not reported for both years."
    else:
        swing = (gm[0] - gm[1]) * 100
        status = FLAG if abs(swing) > GROSS_MARGIN_SWING_PTS else OK
        direction = "rose" if swing > 0 else "fell"
        note = (f"Gross margin {direction} {abs(swing):.1f} points in one year." if status == FLAG else
                f"Gross margin was steady ({swing:+.1f} points).")
    numbers["change_pts"] = None if swing is None else round(swing, 1)
    return _result("F3c", "Gross margin swing", status, numbers,
                   f"Flag if gross margin moves more than {GROSS_MARGIN_SWING_PTS:g} points either way",
                   note, "Cutoff, accuracy",
                   "Possible cost cutoff errors or misclassification.")


def test_profit_vs_cash(ds):
    """F3-d. Profit that isn't turning into cash ("accruals") is a classic
    earnings-quality warning: the profit rests on estimates and receivables
    rather than money received. Auditors look harder at the judgments behind it."""
    ni, ocf, ta = _vals(ds, "net_income"), _vals(ds, "operating_cash_flow"), _vals(ds, "total_assets")
    avg_assets = (ta[0] + ta[1]) / 2 if ta[0] is not None and ta[1] is not None else None
    numbers = {"net_income": ni[0], "operating_cash_flow": ocf[0], "avg_total_assets": avg_assets}
    if ni[0] is None or ocf[0] is None or not avg_assets:
        status, acc = NO_DATA, None
        note = "Net income, operating cash flow, or total assets not reported."
    else:
        acc = (ni[0] - ocf[0]) / avg_assets
        status = FLAG if acc > ACCRUALS_TO_ASSETS else OK
        note = (f"Profit exceeded operating cash flow by {acc*100:.1f}% of assets." if status == FLAG else
                ("Operating cash flow was higher than profit." if acc <= 0 else
                 f"Profit was mostly backed by cash (gap {acc*100:.1f}% of assets)."))
    numbers["accruals_to_assets_pct"] = _pts(acc)
    return _result("F3d", "Profit not backed by cash", status, numbers,
                   f"Flag if (net income - operating cash flow) / average total assets > {ACCRUALS_TO_ASSETS*100:g}%",
                   note, "Occurrence, accuracy", "Earnings quality risk.")


def test_deferred_revenue_direction(ds):
    """F3-e. Deferred revenue is cash collected for work not yet done. If
    revenue jumps while deferred revenue shrinks, the company may be pulling
    future revenue into this year. Auditors test when revenue was recognized."""
    rev, dr = _vals(ds, "revenue"), _vals(ds, "deferred_revenue")
    if all(v is None for v in dr):
        return _result("F3e", "Deferred revenue moving the wrong way", NO_DATA, {},
                       f"Flag if revenue up > {DEFREV_REVENUE_UP*100:g}% while deferred revenue down > {DEFREV_DOWN*100:g}%",
                       "The company reports no deferred revenue, so this test is skipped.",
                       "Cutoff", "Revenue may be getting pulled forward.")
    g_rev, g_dr = growth(rev[0], rev[1]), growth(dr[0], dr[1])
    numbers = {"revenue_growth_pct": _pts(g_rev), "deferred_revenue_growth_pct": _pts(g_dr)}
    if g_rev is None or g_dr is None:
        status = NO_DATA
        note = "Revenue or deferred revenue not reported for both years."
    else:
        status = FLAG if (g_rev > DEFREV_REVENUE_UP and g_dr < -DEFREV_DOWN) else OK
        note = (f"Revenue rose {g_rev*100:.1f}% while deferred revenue fell {abs(g_dr)*100:.1f}%."
                if status == FLAG else
                f"Revenue {g_rev*100:+.1f}%, deferred revenue {g_dr*100:+.1f}%: no conflict.")
    return _result("F3e", "Deferred revenue moving the wrong way", status, numbers,
                   f"Flag if revenue up > {DEFREV_REVENUE_UP*100:g}% while deferred revenue down > {DEFREV_DOWN*100:g}%",
                   note, "Cutoff", "Revenue may be getting pulled forward.")


def test_prior_year_changes(ds):
    """F3-f. A figure for an old year should stay the same in every later
    filing. If it changed, the company restated or reclassified it, and the
    audit team needs to understand why (an error? a change in presentation?)."""
    changes = list(ds.get("restatements") or [])
    numbers = {"changes": [{"item": c.get("label"), "year_end": c.get("end"),
                            "originally": c.get("original"), "now": c.get("current"),
                            "pct": _pts(c.get("pct"))} for c in changes]}
    if not ds.get("fy_ends"):
        status, note = NO_DATA, "No fiscal years available."
    elif changes:
        status = FLAG
        items = sorted({c.get("label") for c in changes})
        note = f"{len(changes)} prior-year figure(s) changed between filings: {', '.join(items)}."
    else:
        status, note = OK, "Prior-year figures match across filings."
    return _result("F3f", "Prior-year numbers changed", status, numbers,
                   "Flag if a prior-year figure differs by more than 1% between filings",
                   note, "Accuracy, presentation",
                   "A restatement or reclassification needs an explanation.")


RED_FLAG_TESTS = [test_receivables_vs_revenue, test_inventory_vs_cost, test_gross_margin_swing,
                  test_profit_vs_cash, test_deferred_revenue_direction, test_prior_year_changes]


def red_flags(ds):
    """All F3 tests, flagged ones first (stable order otherwise)."""
    order = {FLAG: 0, OK: 1, NO_DATA: 2}
    results = [t(ds) for t in RED_FLAG_TESTS]
    return sorted(results, key=lambda r: order[r["status"]])


# ============================================================
#  Everything together
# ============================================================
def analyze(ds):
    """Run F1-F3 on one dataset. Returns a JSON-friendly dict."""
    if not ds or not ds.get("ok"):
        return {"ok": False, "reason": (ds or {}).get("reason", "no_data")}
    mat = materiality(ds)
    flux = fluctuations(ds, mat["amount"])
    flags = red_flags(ds)
    profile = ds.get("profile") or {}
    return {"ok": True, "entity": ds.get("entity"), "fy_ends": ds.get("fy_ends"),
            "limited_support": bool(profile.get("is_financial")),
            "materiality": mat, "fluctuations": flux, "red_flags": flags,
            "summary": {"flags": sum(r["status"] == FLAG for r in flags),
                        "flux_flags": sum(r["flag"] for r in flux)}}
