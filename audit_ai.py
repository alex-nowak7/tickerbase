"""
Audit View F4 - AI summary of the company's revenue recognition policy.

What the AI gets: the revenue policy text from the latest 10-K (see
audit_data.build_policy_text) plus our own red flags. What it returns: a short,
structured JSON summary of how the company makes money, the ASC 606 judgment
areas, and the top audit risks.

ASC 606 is the US revenue recognition standard. Its 5 steps: (1) identify the
contract, (2) identify the performance obligations (the distinct promises),
(3) determine the price, including variable amounts like discounts and returns,
(4) allocate the price to the obligations, (5) recognize revenue when (or as)
each obligation is satisfied. The judgment calls in those steps are where
auditors expect misstatements.

Guardrails, because a language model can make things up:
  - it may use ONLY the filing text and our numbers (system prompt)
  - we validate the JSON shape and trim it to the spec's limits
  - every "evidence" quote is checked against the filing text; quotes that
    don't appear there are dropped
  - malformed output: retry once, then "Summary unavailable"

Everything except run_model() is pure and unit-tested.
"""

import json
import re

# ============================================================
#  Config
# ============================================================
# Chosen 2026-11-10 after comparing on AAPL: Llama 3.1 8B (~35 neurons/summary)
# quoted accurately but reasoned poorly about risk; Gemma 4 26B failed twice;
# Llama 3.3 70B (~215 neurons) gave the clearest, audit-focused output. Each
# summary is cached forever, so the free 10,000 neurons/day covers ~46 new 10-Ks.
MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
# Models that support Workers AI "JSON mode" (output forced to match a schema).
JSON_MODE_MODELS = {"@cf/meta/llama-3.3-70b-instruct-fp8-fast", "@cf/meta/llama-3.1-8b-instruct",
                    "@cf/meta/llama-3-8b-instruct"}
MAX_TOKENS = 900
MAX_ITEMS = 3
MAX_QUOTE_WORDS = 25
PAGE_LABEL = "AI summary of the company's own disclosure. Educational, not an audit opinion."

SYSTEM_PROMPT = (
    "You are helping a student explain audit risk. Use ONLY the filing text and numbers provided. "
    "Do not invent facts. If the text does not cover something, list it in `not_disclosed`. "
    "Write in plain English with short sentences and no hype. Focus on ASC 606 judgment areas: "
    "identifying performance obligations, variable consideration (discounts, rebates, returns), "
    "principal vs. agent, timing (point in time vs. over time), and bundled arrangements. "
    "Return valid JSON only."
)

OUTPUT_TEMPLATE = """{
  "how_they_make_money": "2-3 sentences",
  "judgment_areas": [
    {"area": "Principal vs. agent", "plain_english": "1-2 sentences", "evidence": "short quote from the filing text"}
  ],
  "top_audit_risks": [
    {"risk": "...", "why": "...", "linked_flag": "F3a or null"}
  ],
  "not_disclosed": ["topics the extracted text did not cover"]
}"""

SCHEMA = {
    "type": "object",
    "properties": {
        "how_they_make_money": {"type": "string"},
        "judgment_areas": {"type": "array", "items": {
            "type": "object",
            "properties": {"area": {"type": "string"}, "plain_english": {"type": "string"},
                           "evidence": {"type": "string"}},
            "required": ["area", "plain_english", "evidence"]}},
        "top_audit_risks": {"type": "array", "items": {
            "type": "object",
            "properties": {"risk": {"type": "string"}, "why": {"type": "string"},
                           "linked_flag": {"type": ["string", "null"]}},
            "required": ["risk", "why", "linked_flag"]}},
        "not_disclosed": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["how_they_make_money", "judgment_areas", "top_audit_risks", "not_disclosed"],
}


# ============================================================
#  Building the prompt
# ============================================================
def flag_lines(analysis):
    """Our F2/F3 results, flags only, as short lines for the prompt.
    Returns (lines, valid_flag_ids)."""
    lines, ids = [], []
    for r in (analysis or {}).get("red_flags") or []:
        if r.get("status") == "flag":
            lines.append(f"- {r['id']} {r['name']}: {r['note']}")
            ids.append(r["id"])
    for row in (analysis or {}).get("fluctuations") or []:
        lt = row.get("latest") or {}
        if lt.get("flag"):
            pct = "n/m" if lt.get("pct") is None else f"{lt['pct'] * 100:+.1f}%"
            lines.append(f"- F2 {row['label']} changed {pct} vs prior year (above materiality).")
    if any(l.startswith("- F2") for l in lines):
        ids.append("F2")
    return lines, ids


def build_messages(company, fiscal_year_end, policy_text, analysis):
    lines, ids = flag_lines(analysis)
    flags = "\n".join(lines) if lines else "- No red flags were raised."
    allowed = ", ".join(ids) if ids else "none"
    user = (
        f"Company: {company}\n"
        f"Latest fiscal year end: {fiscal_year_end}\n\n"
        f"Red flags from our analytics (latest year vs prior year):\n{flags}\n\n"
        f"Filing text (revenue recognition policy from the latest 10-K):\n"
        f"<<<\n{policy_text}\n>>>\n\n"
        f"Return JSON in exactly this shape:\n{OUTPUT_TEMPLATE}\n\n"
        f"Rules: at most {MAX_ITEMS} judgment_areas and at most {MAX_ITEMS} top_audit_risks. "
        f"Each evidence value must be an exact quote copied from the filing text, "
        f"{MAX_QUOTE_WORDS} words or fewer. "
        f"top_audit_risks are risks that revenue or a flagged balance is misstated "
        f"(not business or investment risks). linked_flag must be one of: {allowed}, or null, "
        f"and only when the risk is directly about that flag. "
        f"not_disclosed lists only the ASC 606 judgment areas named above that the filing text "
        f"does not discuss. "
        f"Style: write like a business student explaining this to a classmate. Use clear, direct "
        f"sentences. When you are giving an actual reason, you can use 'This means' or 'This is because'. "
        f"Use parentheses for short clarifications. Avoid stiff phrases like 'leading to "
        f"misstatement' or 'may be inaccurate'; say plainly what could go wrong and why. "
        f"Return only the JSON object, with no other text."
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def request_payload(model, messages):
    payload = {"messages": messages, "max_tokens": MAX_TOKENS, "temperature": 0.2}
    if model in JSON_MODE_MODELS:
        payload["response_format"] = {"type": "json_schema", "json_schema": SCHEMA}
    return payload


# ============================================================
#  Checking what comes back
# ============================================================
def _norm(s):
    s = (s or "").lower()
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"[^a-z0-9%$.,'\" ]+", " ", re.sub(r"\s+", " ", s)).split()


def quote_in_text(quote, source):
    """True if the quote really appears in the filing text (ignoring case,
    punctuation spacing, curly quotes, and a trailing '...')."""
    q = [w for w in _norm(quote.rstrip(".… ").strip('"')) if w]
    if len(q) < 3:
        return False
    src = " ".join(_norm(source))
    return " ".join(q) in src


def _trim_words(s, n):
    words = (s or "").split()
    return s.strip() if len(words) <= n else " ".join(words[:n]) + " …"


def _extract_json(raw):
    """Accept a dict (JSON mode) or a string that may wrap the JSON in prose
    or ``` fences."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except ValueError:
        pass
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(raw[start:end + 1])
    except ValueError:
        return None


def validate(raw, source_text, valid_flags):
    """Return (summary, None) if usable, else (None, reason).

    Cleans as it goes: trims lists to 3 items, trims quotes to 25 words,
    drops quotes that are not in the filing, and resets unknown flag IDs.
    """
    data = _extract_json(raw)
    if not isinstance(data, dict):
        return None, "not JSON"
    how = data.get("how_they_make_money")
    if not isinstance(how, str) or not how.strip():
        return None, "missing how_they_make_money"

    areas = []
    for a in data.get("judgment_areas") or []:
        if not isinstance(a, dict) or not str(a.get("area") or "").strip():
            continue
        ev = _trim_words(str(a.get("evidence") or "").strip().strip('"'), MAX_QUOTE_WORDS)
        verified = bool(ev) and quote_in_text(ev.rstrip(" …"), source_text)
        areas.append({"area": str(a["area"]).strip(),
                      "plain_english": str(a.get("plain_english") or "").strip(),
                      "evidence": ev if verified else None})
    risks = []
    for r in data.get("top_audit_risks") or []:
        if not isinstance(r, dict) or not str(r.get("risk") or "").strip():
            continue
        flag = r.get("linked_flag")
        flag = flag.strip() if isinstance(flag, str) else None
        if flag not in valid_flags:
            flag = None
        risks.append({"risk": str(r["risk"]).strip(), "why": str(r.get("why") or "").strip(),
                      "linked_flag": flag})
    nd = [str(x).strip() for x in (data.get("not_disclosed") or []) if str(x).strip()]

    if not areas and not risks:
        return None, "no judgment areas or risks"
    return {"how_they_make_money": how.strip(), "judgment_areas": areas[:MAX_ITEMS],
            "top_audit_risks": risks[:MAX_ITEMS], "not_disclosed": nd[:6]}, None


# ============================================================
#  Calling the model (the only impure part)
# ============================================================
async def summarize(run_model, model, messages, source_text, valid_flags, attempts=2):
    """run_model: async (model, payload) -> raw output (dict or str).
    Tries up to `attempts` times (= the first try plus one retry).
    Returns {ok, summary | reason, model, attempts}."""
    payload = request_payload(model, messages)
    reason = "not run"
    for i in range(attempts):
        try:
            raw = await run_model(model, payload)
        except Exception as e:  # noqa: BLE001  e.g. "JSON Mode couldn't be met"
            reason = f"model error: {str(e)[:160]}"
            continue
        summary, reason = validate(raw, source_text, valid_flags)
        if summary:
            return {"ok": True, "summary": summary, "model": model, "attempts": i + 1}
    return {"ok": False, "reason": reason, "model": model, "attempts": attempts}


def worker_runner(ai_binding):
    """Wraps env.AI for summarize(). Converts Python <-> JavaScript objects."""
    async def run(model, payload):
        from js import Object
        from pyodide.ffi import to_js
        result = await ai_binding.run(model, to_js(payload, dict_converter=Object.fromEntries))
        if hasattr(result, "to_py"):
            result = result.to_py()
        if isinstance(result, dict):
            return result.get("response", result)
        return result
    return run
