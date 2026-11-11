"""Tests for audit_view.py (HTML rendering) and the landing page wiring."""

import asyncio

import audit_analytics as aa
import audit_view as av
import edgar_client as ec
from replay import replay_http


def analysis_for(ticker, cik):
    ds = asyncio.run(ec.load_financials(ec.Edgar(replay_http(ticker), min_interval=0), cik))
    return aa.analyze(ds)


def test_render_aapl_has_every_section_in_order():
    html = av.render_audit_view("AAPL", analysis_for("AAPL", 320193))
    order = ["Apple Inc.", "Source: SEC EDGAR (XBRL)", "Planning materiality", "Red-flag tests",
             "Fluctuation analysis", 'id="audit-ai"', "How this works"]
    positions = [html.find(s) for s in order]
    assert all(p >= 0 for p in positions) and positions == sorted(positions)
    assert "$8.0B" in html and "FY24–FY26" in html
    # flagged tests come before ok ones
    assert html.find("Inventory growing faster") < html.find("Receivables growing faster")


def test_render_wmt_shows_not_reported_instead_of_errors():
    html = av.render_audit_view("WMT", analysis_for("WMT", 104169))
    assert "Not reported" in html
    assert "(calculated)" in html             # derived gross profit is labelled


def test_no_xbrl_company_gets_one_message():
    html = av.render_audit_view("XYZ", aa.analyze({"ok": False, "reason": "no_xbrl_revenue"}))
    assert html.count("av-empty") == 1 and "20-F" in html


def test_bank_banner():
    out = analysis_for("AAPL", 320193)
    out["limited_support"] = True
    assert "Limited support for banks and insurers" in av.render_audit_view("JPM", out)


def test_summary_rendering_and_escaping():
    payload = {"ok": True, "label": "AI summary of the company's own disclosure.", "accession": "0001",
               "source": "revenue policy",
               "summary": {"how_they_make_money": "Sells <b>phones</b>.",
                           "judgment_areas": [{"area": "Timing", "plain_english": "x", "evidence": "a quote"},
                                              {"area": "Returns", "plain_english": "y", "evidence": None}],
                           "top_audit_risks": [{"risk": "R", "why": "W", "linked_flag": "F3b"}],
                           "not_disclosed": ["Principal vs. agent"]}}
    html = av.render_summary(payload)
    assert "&lt;b&gt;phones" in html                  # model text is escaped, never raw HTML
    assert html.count('class="q"') == 1               # dropped quote is simply not shown
    assert "F3b" in html and "Principal vs. agent" in html
    assert av.render_summary({"ok": False}) == '<p class="av-note">Summary unavailable.</p>'


def test_landing_page_has_switch_and_audit_container():
    import landing
    page = landing.LANDING_PAGE
    assert 'id="viewsw"' in page and 'id="audit"' in page and "/audit/summary" in page
    assert ".av-table" in page
