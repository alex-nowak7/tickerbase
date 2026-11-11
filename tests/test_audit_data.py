"""Tests for the EDGAR data layer (audit_data.py + edgar_client.py).

Run:  .venv\\Scripts\\python.exe -m pytest -q
"""

import asyncio

import pytest

import audit_data as ad
import edgar_client as ec
from replay import replay_http

AAPL, WMT = 320193, 104169


def run(coro):
    return asyncio.run(coro)


def edgar_for(ticker):
    return ec.Edgar(replay_http(ticker), min_interval=0)


def fact(end, val, filed, accn, start=None, form="10-K"):
    f = {"end": end, "val": val, "filed": filed, "accn": accn, "form": form}
    if start:
        f["start"] = start
    return f


def concept(*facts):
    return {"units": {"USD": list(facts)}}


# ------------------------------------------------------------------ filtering
def test_annual_flow_keeps_full_years_from_10k_only():
    j = concept(
        fact("2025-12-31", 100, "2026-02-01", "a", start="2025-01-01"),               # full year
        fact("2025-12-31", 30, "2026-02-01", "a", start="2025-10-01"),                # Q4 only
        fact("2025-09-30", 70, "2025-11-01", "q", start="2025-01-01", form="10-Q"),   # 10-Q
        fact("2025-12-31", 5, "2026-02-01", "a"),                                     # instant
    )
    got = ad.annual_facts(j, "flow")
    assert [f["val"] for f in got] == [100]


def test_annual_instant_keeps_point_in_time_only():
    j = concept(fact("2025-12-31", 5, "2026-02-01", "a"),
                fact("2025-12-31", 100, "2026-02-01", "a", start="2025-01-01"))
    assert [f["val"] for f in ad.annual_facts(j, "instant")] == [5]


def test_annual_facts_handles_missing_json():
    assert ad.annual_facts(None, "flow") == []
    assert ad.annual_facts({"units": {}}, "flow") == []


# ------------------------------------------------------------------ year-ends and tags
def test_fiscal_year_ends_newest_first_and_deduped():
    facts = [{"end": e} for e in ("2023-09-30", "2024-09-28", "2025-09-27", "2026-09-26", "2026-09-26")]
    assert ad.fiscal_year_ends(facts) == ["2026-09-26", "2025-09-27", "2024-09-28"]


def test_choose_tag_skips_abandoned_tag():
    stale = [{"end": "2017-09-30"}]
    current = [{"end": "2026-09-26"}]
    # without anchor: most recent data wins even if listed second
    assert ad.choose_tag([("Old", stale), ("New", current)])[0] == "New"
    # with anchor: first tag that covers the latest year
    assert ad.choose_tag([("Old", stale), ("New", current)], "2026-09-26")[0] == "New"
    assert ad.choose_tag([("A", current), ("B", current)], "2026-09-26")[0] == "A"
    assert ad.choose_tag([("A", []), ("B", [])]) == (None, [])


# ------------------------------------------------------------------ resolving values
def test_resolve_uses_most_recent_filing_and_flags_restatement():
    facts = [
        {"end": "2024-12-31", "val": 1000, "filed": "2025-02-01", "accn": "f2024", "form": "10-K"},
        {"end": "2024-12-31", "val": 1050, "filed": "2026-02-01", "accn": "f2025", "form": "10-K"},
        {"end": "2025-12-31", "val": 1200, "filed": "2026-02-01", "accn": "f2025", "form": "10-K"},
    ]
    values, changes = ad.resolve_values(facts, ["2025-12-31", "2024-12-31", "2023-12-31"])
    assert values == [1200, 1050, None]              # missing year -> None
    assert len(changes) == 1
    c = changes[0]
    assert (c["end"], c["current"], c["original"]) == ("2024-12-31", 1050, 1000)
    assert c["pct"] == pytest.approx(50 / 1050, abs=1e-4)


def test_resolve_ignores_tiny_differences():
    facts = [
        {"end": "2024-12-31", "val": 1000, "filed": "2025-02-01", "accn": "a", "form": "10-K"},
        {"end": "2024-12-31", "val": 1005, "filed": "2026-02-01", "accn": "b", "form": "10-K"},
    ]
    _, changes = ad.resolve_values(facts, ["2024-12-31"])
    assert changes == []


def test_resolve_prior_value_of_zero():
    facts = [
        {"end": "2024-12-31", "val": 50, "filed": "2025-02-01", "accn": "a", "form": "10-K"},
        {"end": "2024-12-31", "val": 0, "filed": "2026-02-01", "accn": "b", "form": "10-K"},
    ]
    values, changes = ad.resolve_values(facts, ["2024-12-31"])
    assert values == [0]
    assert changes[0]["pct"] is None                 # can't express a % change from zero


# ------------------------------------------------------------------ real companies (fixtures)
def test_aapl_dataset():
    ds = run(ec.load_financials(edgar_for("AAPL"), AAPL))
    assert ds["ok"]
    assert ds["entity"] == "Apple Inc."
    assert ds["fy_ends"] == ["2026-09-26", "2025-09-27", "2024-09-28"]
    it = ds["items"]
    assert it["revenue"]["tag"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert [round(v / 1e6) for v in it["revenue"]["values"]] == [478834, 416161, 391035]
    assert it["cost_of_revenue"]["tag"] == "CostOfGoodsAndServicesSold"   # CostOfRevenue is a 404
    assert it["gross_profit"]["derived"] is False
    assert ds["missing"] == []
    assert ds["profile"]["is_financial"] is False
    assert ds["latest_10k"]["accession"] == "0000320193-26-000026"
    assert ds["sec_requests"] < 45


def test_wmt_dataset_handles_untagged_items():
    ds = run(ec.load_financials(edgar_for("WMT"), WMT))
    it = ds["items"]
    assert ds["fy_ends"][0] == "2026-01-31"
    # Walmart doesn't tag gross profit: we derive it
    assert it["gross_profit"]["derived"] is True
    assert it["gross_profit"]["values"][0] == it["revenue"]["values"][0] - it["cost_of_revenue"]["values"][0]
    # receivables come from the fallback tag
    assert it["accounts_receivable"]["tag"] == "ReceivablesNetCurrent"
    # no deferred revenue line -> reported as missing, not an error
    assert "deferred_revenue" in ds["missing"]
    assert it["deferred_revenue"]["values"] == [None, None, None]


def test_aapl_policy_text():
    pol = run(ec.load_policy_text(edgar_for("AAPL"), AAPL))
    assert pol["ok"] and pol["accession"] == "0000320193-26-000026"
    assert pol["pages"] == {"policies": "R26.htm", "note": "R10.htm"}
    t = pol["text"]
    assert "performance obligation" in t.lower()
    assert "Namespace Prefix" not in t               # hidden XBRL definitions stripped
    assert len(t) <= ad.POLICY_TEXT_CAP + 2


def test_wmt_policy_text_without_revenue_note():
    pol = run(ec.load_policy_text(edgar_for("WMT"), WMT))
    assert pol["ok"] and pol["pages"]["note"] is None
    assert pol["text"].startswith("Revenue Recognition")
    assert pol["source"] == "revenue policy"


# ------------------------------------------------------------------ small pure pieces
def test_pick_revenue_pages_skips_segment_pages():
    reports = [
        {"short_name": "Segments and Disaggregated Revenue", "html_file": "R19.htm", "category": "Notes"},
        {"short_name": "Summary of Significant Accounting Policies (Policies)", "html_file": "R24.htm", "category": "Policies"},
    ]
    assert ad.pick_revenue_pages(reports) == {"policies": "R24.htm", "note": None}


def test_policy_text_is_capped():
    big = '<tr class="re"><td class="pl">Revenue</td><td class="text">' + "word " * 5000 + "</td></tr>"
    text, source = ad.build_policy_text(big, None, cap=1000)
    assert len(text) <= 1002 and text.endswith("…")
    assert source == "revenue policy"


def test_policy_text_falls_back_to_whole_page():
    page = '<tr class="re"><td class="pl">Leases</td><td class="text">We lease stores.</td></tr>'
    text, source = ad.build_policy_text(page, None)
    assert text == "We lease stores."
    assert source.startswith("full accounting policies")


def test_company_profile_flags_banks_and_insurers():
    assert ad.company_profile({"sic": "6021"})["is_financial"] is True     # national bank
    assert ad.company_profile({"sic": "6331"})["is_financial"] is True     # P&C insurer
    assert ad.company_profile({"sic": "3523"})["is_financial"] is False    # Deere (farm machinery)
    assert ad.company_profile({})["is_financial"] is False


def test_latest_10k_none_for_foreign_filer():
    subs = {"filings": {"recent": {"form": ["20-F", "6-K"], "accessionNumber": ["x", "y"],
                                   "primaryDocument": ["a", "b"]}}}
    assert ad.latest_10k(subs) is None


def test_no_revenue_means_not_ok():
    ds = ad.build_dataset({"revenue": [("Revenues", None)]}, entity_name="X")
    assert ds == {"ok": False, "reason": "no_xbrl_revenue", "entity": "X", "cik": None}


def test_subrequest_budget_enforced():
    async def fake(url):
        return 404, None
    e = ec.Edgar(fake, min_interval=0, budget=2)
    run(e.get("u1"))
    run(e.get("u2"))
    with pytest.raises(ec.BudgetExceeded):
        run(e.get("u3"))
