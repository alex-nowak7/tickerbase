"""Tests for audit_ai.py (prompt building and checking the model's output)."""

import asyncio
import json

import audit_ai as ai

SOURCE = ("The Company recognizes revenue at the amount to which it expects to be entitled when "
          "control of products or services is transferred to its customers. For arrangements with "
          "multiple performance obligations, the Company allocates revenue based on relative "
          "stand-alone selling prices.")

GOOD = {
    "how_they_make_money": "They sell devices and services.",
    "judgment_areas": [
        {"area": "Bundled arrangements", "plain_english": "Price is split across promises.",
         "evidence": "the Company allocates revenue based on relative stand-alone selling prices"},
        {"area": "Timing", "plain_english": "Revenue when control passes.",
         "evidence": "This sentence is not in the filing at all"},
    ],
    "top_audit_risks": [{"risk": "SSP estimates", "why": "Judgment", "linked_flag": "F3b"},
                        {"risk": "Returns", "why": "Estimates", "linked_flag": "F9z"}],
    "not_disclosed": ["Principal vs. agent"],
}

ANALYSIS = {"red_flags": [{"id": "F3b", "name": "Inventory growing faster than cost of sales",
                           "status": "flag", "note": "Inventory grew 143 points faster."},
                          {"id": "F3a", "name": "Receivables", "status": "ok", "note": "fine"}],
            "fluctuations": [{"label": "Revenue", "latest": {"flag": True, "pct": 0.151}},
                             {"label": "SG&A", "latest": {"flag": False, "pct": 0.079}}]}


def test_flag_lines_only_include_flags():
    lines, ids = ai.flag_lines(ANALYSIS)
    assert ids == ["F3b", "F2"]
    assert any("F3b" in l for l in lines) and any("Revenue changed +15.1%" in l for l in lines)
    assert not any("SG&A" in l or "F3a" in l for l in lines)


def test_build_messages_contains_rules_and_text():
    msgs = ai.build_messages("Apple Inc.", "2026-09-26", SOURCE, ANALYSIS)
    assert msgs[0]["content"] == ai.SYSTEM_PROMPT
    u = msgs[1]["content"]
    assert SOURCE in u and "F3b, F2, or null" in u and "25 words or fewer" in u


def test_json_mode_only_for_supported_models():
    assert "response_format" in ai.request_payload("@cf/meta/llama-3.3-70b-instruct-fp8-fast", [])
    assert "response_format" not in ai.request_payload("@cf/google/gemma-4-26b-a4b-it", [])


def test_validate_cleans_output():
    s, err = ai.validate(GOOD, SOURCE, ["F3b", "F2"])
    assert err is None
    assert s["judgment_areas"][0]["evidence"].startswith("the Company allocates")
    assert s["judgment_areas"][1]["evidence"] is None          # invented quote dropped
    assert s["top_audit_risks"][0]["linked_flag"] == "F3b"
    assert s["top_audit_risks"][1]["linked_flag"] is None      # unknown flag id reset


def test_validate_accepts_json_wrapped_in_prose():
    raw = "Here you go:\n```json\n" + json.dumps(GOOD) + "\n```"
    s, err = ai.validate(raw, SOURCE, [])
    assert err is None and s["how_they_make_money"]


def test_validate_trims_lists_and_long_quotes():
    many = dict(GOOD, judgment_areas=[{"area": f"A{i}", "plain_english": "x", "evidence": "word " * 40}
                                      for i in range(5)],
                top_audit_risks=[{"risk": f"R{i}", "why": "y", "linked_flag": None} for i in range(5)])
    s, _ = ai.validate(many, "word " * 100, [])
    assert len(s["judgment_areas"]) == 3 and len(s["top_audit_risks"]) == 3
    assert len(s["judgment_areas"][0]["evidence"].split()) == 26   # 25 words + "…"


def test_validate_rejects_bad_output():
    assert ai.validate("not json at all", SOURCE, [])[0] is None
    assert ai.validate({"judgment_areas": []}, SOURCE, [])[0] is None
    assert ai.validate({"how_they_make_money": "x", "judgment_areas": [], "top_audit_risks": []},
                       SOURCE, [])[0] is None


def test_quote_matching_is_forgiving_about_formatting():
    src = "Revenue is recognized when control transfers — generally upon shipment."
    assert ai.quote_in_text("revenue is recognized when control transfers", src)
    assert ai.quote_in_text("Revenue is recognized when control transfers...", src)
    assert not ai.quote_in_text("revenue is recognized at year end", src)
    assert not ai.quote_in_text("Revenue", src)                 # too short to count


def test_summarize_retries_once_then_gives_up():
    calls = []

    async def bad_then_good(model, payload):
        calls.append(1)
        return "oops" if len(calls) == 1 else GOOD

    out = asyncio.run(ai.summarize(bad_then_good, "m", [], SOURCE, []))
    assert out["ok"] and out["attempts"] == 2

    calls.clear()

    async def always_bad(model, payload):
        calls.append(1)
        raise RuntimeError("JSON Mode couldn't be met")

    out = asyncio.run(ai.summarize(always_bad, "m", [], SOURCE, []))
    assert out["ok"] is False and len(calls) == 2 and "JSON Mode" in out["reason"]
