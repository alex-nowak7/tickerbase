"""Tests for audit_analytics.py (F1 materiality, F2 fluctuations, F3 red flags)."""

import asyncio

import pytest

import audit_analytics as aa
import edgar_client as ec
from replay import replay_http

M = 1_000_000


def make_ds(restatements=None, **items):
    """Build a dataset like audit_data.build_dataset() returns.
    Each kwarg is item_key=[latest, prior, two_years_back] in millions."""
    return {"ok": True, "entity": "TestCo", "fy_ends": ["2025-12-31", "2024-12-31", "2023-12-31"],
            "items": {k: {"label": k.replace("_", " ").title(), "values": [None if x is None else x * M for x in v],
                          "derived": False} for k, v in items.items()},
            "restatements": restatements or [], "profile": {"is_financial": False}}


BASE = dict(revenue=[1000, 900, 800], cost_of_revenue=[600, 540, 480], gross_profit=[400, 360, 320],
            pretax_income=[100, 90, 80], net_income=[80, 72, 64], operating_cash_flow=[90, 80, 70],
            accounts_receivable=[100, 90, 80], inventory=[150, 135, 120],
            deferred_revenue=[50, 45, 40], total_assets=[2000, 1900, 1800])


def ds_with(**overrides):
    return make_ds(**{**BASE, **overrides})


def by_id(results, test_id):
    return next(r for r in results if r["id"] == test_id)


# ------------------------------------------------------------------ F1
def test_materiality_uses_pretax_income():
    m = aa.materiality(ds_with())
    assert m["benchmark"] == "pretax_income"
    assert m["amount"] == pytest.approx(5 * M)          # 5% of 100m


def test_materiality_falls_back_to_revenue_on_loss():
    m = aa.materiality(ds_with(pretax_income=[-50, 10, 20]))
    assert m["benchmark"] == "revenue"
    assert m["amount"] == pytest.approx(5 * M)          # 0.5% of 1,000m
    assert "loss" in m["reason"]


def test_materiality_falls_back_when_profit_is_tiny():
    m = aa.materiality(ds_with(pretax_income=[5, 10, 20]))   # 0.5% of revenue < 1%
    assert m["benchmark"] == "revenue"
    assert "under 1%" in m["reason"]


def test_materiality_without_pretax_or_revenue():
    m = aa.materiality(make_ds(net_income=[1, 2, 3]))
    assert m["status"] == aa.NO_DATA and m["amount"] is None


# ------------------------------------------------------------------ F2
def test_compare_flags_only_when_both_big():
    assert aa.compare(120 * M, 100 * M, 5 * M)["flag"] is True          # +20m, +20%
    assert aa.compare(102 * M, 100 * M, 1 * M)["flag"] is False         # 2%: too small in %
    assert aa.compare(1.5 * M, 1 * M, 5 * M)["flag"] is False           # 50% but only 0.5m


def test_compare_prior_zero():
    r = aa.compare(10 * M, 0, 5 * M)
    assert r["pct"] is None and r["flag"] is True                       # from zero counts as a big %


def test_compare_negative_prior_uses_absolute_base():
    r = aa.compare(-50 * M, -100 * M, 5 * M)                            # loss halved
    assert r["pct"] == pytest.approx(0.5)


def test_fluctuations_missing_year():
    rows = aa.fluctuations(ds_with(sga=[100, None, 80]), 5 * M)
    sga = next(r for r in rows if r["key"] == "sga")
    assert sga["latest"]["status"] == aa.NO_DATA and sga["earlier"]["status"] == aa.NO_DATA
    assert sga["flag"] is False


def test_fluctuations_add_note_and_assertion_only_when_flagged():
    rows = aa.fluctuations(ds_with(), 5 * M)
    rev = next(r for r in rows if r["key"] == "revenue")
    assert rev["flag"] is True and rev["assertion"] == "Existence" and rev["why"]
    ta = next(r for r in rows if r["key"] == "total_assets")    # +5.3%: not flagged
    assert ta["flag"] is False and ta["why"] is None


# ------------------------------------------------------------------ F3-a receivables
def test_f3a_flags_receivables_outgrowing_revenue():
    r = aa.test_receivables_vs_revenue(ds_with(accounts_receivable=[130, 90, 80]))   # +44% vs +11%
    assert r["status"] == aa.FLAG
    assert r["numbers"]["dso_latest"] == pytest.approx(47.5, abs=0.1)


def test_f3a_ok_and_no_data():
    assert aa.test_receivables_vs_revenue(ds_with())["status"] == aa.OK
    assert aa.test_receivables_vs_revenue(ds_with(accounts_receivable=[None, None, None]))["status"] == aa.NO_DATA


# ------------------------------------------------------------------ F3-b inventory
def test_f3b_flags_inventory_build():
    assert aa.test_inventory_vs_cost(ds_with(inventory=[200, 135, 120]))["status"] == aa.FLAG


def test_f3b_no_inventory():
    r = aa.test_inventory_vs_cost(ds_with(inventory=[None, None, None]))
    assert r["status"] == aa.NO_DATA and "doesn't report inventory" in r["note"]


# ------------------------------------------------------------------ F3-c gross margin
def test_f3c_flags_margin_swing_either_way():
    assert aa.test_gross_margin_swing(ds_with(gross_profit=[450, 360, 320]))["status"] == aa.FLAG   # 45% vs 40%
    assert aa.test_gross_margin_swing(ds_with(gross_profit=[350, 360, 320]))["status"] == aa.FLAG   # 35% vs 40%
    assert aa.test_gross_margin_swing(ds_with())["status"] == aa.OK                                 # 40% both


# ------------------------------------------------------------------ F3-d profit vs cash
def test_f3d_flags_profit_without_cash():
    r = aa.test_profit_vs_cash(ds_with(net_income=[300, 72, 64], operating_cash_flow=[50, 80, 70]))
    assert r["status"] == aa.FLAG                           # 250 / 1950 = 12.8%
    assert r["numbers"]["accruals_to_assets_pct"] == pytest.approx(12.8, abs=0.1)


def test_f3d_ok_when_cash_exceeds_profit():
    r = aa.test_profit_vs_cash(ds_with())
    assert r["status"] == aa.OK and "higher than profit" in r["note"]


def test_f3d_missing_prior_assets():
    assert aa.test_profit_vs_cash(ds_with(total_assets=[2000, None, 1800]))["status"] == aa.NO_DATA


# ------------------------------------------------------------------ F3-e deferred revenue
def test_f3e_flags_revenue_up_deferred_down():
    r = aa.test_deferred_revenue_direction(ds_with(revenue=[1100, 900, 800], deferred_revenue=[35, 45, 40]))
    assert r["status"] == aa.FLAG


def test_f3e_skipped_without_deferred_revenue():
    r = aa.test_deferred_revenue_direction(ds_with(deferred_revenue=[None, None, None]))
    assert r["status"] == aa.NO_DATA and "skipped" in r["note"]


def test_f3e_prior_zero_is_not_enough_data():
    r = aa.test_deferred_revenue_direction(ds_with(deferred_revenue=[10, 0, 0]))
    assert r["status"] == aa.NO_DATA


# ------------------------------------------------------------------ F3-f prior-year changes
def test_f3f_flags_restatement():
    ds = make_ds(restatements=[{"item": "revenue", "label": "Revenue", "end": "2024-12-31",
                                "current": 900 * M, "original": 950 * M, "pct": 0.0556}], **BASE)
    r = aa.test_prior_year_changes(ds)
    assert r["status"] == aa.FLAG and "Revenue" in r["note"]
    assert r["numbers"]["changes"][0]["pct"] == pytest.approx(5.6)


def test_f3f_ok_when_nothing_changed():
    assert aa.test_prior_year_changes(ds_with())["status"] == aa.OK


# ------------------------------------------------------------------ putting it together
def test_red_flags_sorted_flags_first():
    res = aa.red_flags(ds_with(accounts_receivable=[130, 90, 80], inventory=[None, None, None]))
    statuses = [r["status"] for r in res]
    assert statuses[0] == aa.FLAG and statuses[-1] == aa.NO_DATA
    assert len(res) == 6


def test_analyze_not_ok_dataset():
    assert aa.analyze({"ok": False, "reason": "no_xbrl_revenue"}) == {"ok": False, "reason": "no_xbrl_revenue"}


def test_analyze_marks_banks_as_limited():
    ds = ds_with()
    ds["profile"] = {"is_financial": True}
    assert aa.analyze(ds)["limited_support"] is True


def test_analyze_real_wmt_never_crashes():
    ds = asyncio.run(ec.load_financials(ec.Edgar(replay_http("WMT"), min_interval=0), 104169))
    out = aa.analyze(ds)
    assert out["ok"] and out["materiality"]["benchmark"] == "pretax_income"
    assert by_id(out["red_flags"], "F3e")["status"] == aa.NO_DATA      # Walmart: no deferred revenue line
