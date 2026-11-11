"""
Audit View - HTML rendering.

Builds the Audit View tab from the outputs of audit_analytics.analyze() and the
F4 summary. Reuses the existing Tickerbase design (section cards, pills, metric
tiles); AUDIT_CSS only adds the few pieces that didn't exist yet.
"""

import tickerbase as sl
from tickerbase import esc

NOT_REPORTED = "Not reported"

AUDIT_CSS = """
.viewsw{display:flex;justify-content:center;gap:0;margin:4px auto 18px;width:max-content;
  border:1px solid var(--border2);border-radius:22px;padding:3px;background:var(--surface);}
.viewsw[hidden]{display:none;}
.viewsw button{font-size:13.5px;font-weight:600;padding:8px 18px;border-radius:18px;border:none;
  background:transparent;color:var(--muted);cursor:pointer;transition:background .12s ease,color .12s ease;}
.viewsw button.on{background:var(--accent);color:#fff;}
.viewsw button:not(.on):hover{background:var(--surface2);color:var(--ink);}
.av-head{text-align:center;margin:0 0 18px;}
.av-head h2{font-size:21px;margin:0 0 4px;}
.av-head p{font-size:12.5px;color:var(--muted);margin:0;font-variant-numeric:tabular-nums;}
.av-note{font-size:13px;color:var(--muted);line-height:1.6;margin:10px 0 0;}
.av-banner{background:var(--amber-bg);color:var(--amber);border-radius:var(--r-sm);padding:10px 14px;
  font-size:13.5px;font-weight:600;margin:0 0 12px;text-align:center;}
.av-empty{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:28px 22px;
  text-align:center;color:var(--muted);font-size:14.5px;line-height:1.6;}
.pill.na{background:var(--surface2);color:var(--muted);}
.av-flag{border-top:1px solid var(--border);padding:14px 0;}
.av-flag:first-child{border-top:none;padding-top:4px;}
.av-flag .t{display:flex;align-items:center;gap:9px;flex-wrap:wrap;font-weight:600;font-size:14.5px;}
.av-flag .t .id{font-size:11.5px;color:var(--hint);font-weight:600;font-variant-numeric:tabular-nums;}
.av-flag .n{font-size:13.5px;margin:6px 0 0;line-height:1.55;}
.av-flag .nums{display:flex;flex-wrap:wrap;gap:6px 16px;margin:7px 0 0;font-size:12.5px;color:var(--muted);
  font-variant-numeric:tabular-nums;}
.av-flag .nums b{color:var(--ink);font-weight:600;}
.av-flag .a{font-size:12.5px;color:var(--muted);margin:6px 0 0;}
.av-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:0 -4px;padding:0 4px;}
.av-table{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums;min-width:640px;}
.av-table th,.av-table td{padding:8px 10px;text-align:right;border-bottom:1px solid var(--border);white-space:nowrap;}
.av-table th{font-size:11.5px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);font-weight:600;}
.av-table th:first-child,.av-table td:first-child{text-align:left;}
.av-table tr.fl td{background:var(--red-bg);}
.av-table td.chg.fl{color:var(--red);font-weight:700;}
.av-table td.nr{color:var(--hint);}
.av-table .calc{font-size:11px;color:var(--hint);font-weight:400;}
.av-why{font-size:12.5px;color:var(--muted);line-height:1.55;margin:12px 0 0;padding-left:16px;}
.av-why li{margin:0 0 6px;}
.av-ai h4{font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:18px 0 8px;}
.av-ai .lbl{font-size:12px;color:var(--hint);margin:0 0 12px;font-style:italic;}
.av-ai .item{margin:0 0 12px;line-height:1.55;font-size:14px;}
.av-ai .q{display:block;font-size:12.5px;color:var(--muted);border-left:3px solid var(--border2);
  padding:2px 0 2px 10px;margin:5px 0 0;font-style:italic;}
.av-ai .link{font-size:11px;font-weight:700;color:var(--red);background:var(--red-bg);padding:1px 7px;
  border-radius:10px;margin-left:6px;}
.av-how ol{margin:6px 0 0;padding-left:20px;line-height:1.65;font-size:14px;}
.av-how li{margin:0 0 6px;}
"""

# ------------------------------------------------------------------ formatting
def money(v):
    if v is None:
        return NOT_REPORTED
    a = abs(v)
    if a >= 1e9:
        s = f"${a / 1e9:,.1f}B"
    elif a >= 1e6:
        s = f"${a / 1e6:,.0f}M"
    else:
        s = f"${a:,.0f}"
    return "−" + s if v < 0 else s


def pct(p, signed=True):
    if p is None:
        return "n/m"
    return f"{p * 100:+.1f}%" if signed else f"{p * 100:.1f}%"


def year_label(end):
    return f"FY{end[2:4]}" if end else ""


STATUS = {"flag": ("bad", "Flag"), "ok": ("good", "OK"), "not enough data": ("na", "Not enough data")}

# How to show each number a red-flag test returns: key -> (label, kind)
NUMBER_LABELS = {
    "revenue_growth_pct": ("Revenue growth", "pct"),
    "ar_growth_pct": ("Receivables growth", "pct"),
    "dso_latest": ("Days sales outstanding, latest", "days"),
    "dso_prior": ("prior", "days"),
    "cost_growth_pct": ("Cost of revenue growth", "pct"),
    "inventory_growth_pct": ("Inventory growth", "pct"),
    "days_inventory_latest": ("Days of inventory, latest", "days"),
    "days_inventory_prior": ("prior", "days"),
    "gross_margin_latest_pct": ("Gross margin, latest", "pctplain"),
    "gross_margin_prior_pct": ("prior", "pctplain"),
    "net_income": ("Net income", "money"),
    "operating_cash_flow": ("Operating cash flow", "money"),
    "avg_total_assets": ("Average total assets", "money"),
    "accruals_to_assets_pct": ("Gap as % of assets", "pctplain"),
    "deferred_revenue_growth_pct": ("Deferred revenue growth", "pct"),
}


def _num(kind, v):
    if v is None:
        return NOT_REPORTED
    if kind == "pct":
        return f"{v:+.1f}%"
    if kind == "pctplain":
        return f"{v:.1f}%"
    if kind == "days":
        return f"{v:.0f} days"
    return money(v)


ASSERTION_TIP = sl.tip2(
    "Assertions are the claims built into every number in the financial statements: that it exists, "
    "is complete, is accurate, is recorded in the right period (cutoff), and is valued correctly.",
    "Auditors design their tests around the assertion most likely to be wrong. "
    "Each flag here names the one at risk.")


# ------------------------------------------------------------------ pieces
def render_header(out, ticker):
    ends = out.get("fy_ends") or []
    years = f"{year_label(ends[-1])}–{year_label(ends[0])}" if ends else ""
    return (f'<div class="av-head"><h2>{esc(out.get("entity") or ticker)}</h2>'
            f'<p>Audit planning view · fiscal years {esc(years)} '
            f'(latest ending {esc(ends[0] if ends else "")}) · Source: SEC EDGAR (XBRL)</p></div>')


def render_materiality(m):
    if m.get("amount") is None:
        body = f'<p class="av-note">{esc(m.get("reason") or "Not enough data to set materiality.")}</p>'
    else:
        tiles = "".join([
            sl.metric("Planning materiality", esc(money(m["amount"])),
                      sl.tip2("The size of a mistake that would change a reader's decision about the "
                              "financial statements.",
                              "Differences smaller than this are usually not worth chasing; "
                              "balances and swings bigger than this get attention.")),
            sl.metric("Benchmark", esc(m["benchmark_label"])),
            sl.metric("Benchmark value", esc(money(m["benchmark_value"]))),
        ])
        reason = f'<p class="av-note">{esc(m["reason"])}</p>' if m.get("reason") else ""
        body = f'<div class="mgrid">{tiles}</div>{reason}<p class="av-note">{esc(m["note"])}</p>'
    return sl.section("av-mat", "value", "Planning materiality",
                      "How big a mistake has to be to matter", None, body, open_default=True)


def render_red_flags(flags):
    rows = []
    for r in flags:
        cls, label = STATUS[r["status"]]
        nums = []
        for k, v in (r.get("numbers") or {}).items():
            if k in NUMBER_LABELS:
                lab, kind = NUMBER_LABELS[k]
                nums.append(f"<span>{esc(lab)} <b>{esc(_num(kind, v))}</b></span>")
        for c in (r.get("numbers") or {}).get("changes", [])[:5]:
            nums.append(f"<span>{esc(c['item'])}, year ending {esc(c['year_end'])}: "
                        f"<b>{esc(money(c['originally']))} → {esc(money(c['now']))}</b></span>")
        why = f" {esc(r['why'])}" if r["status"] == "flag" else ""
        rows.append(
            f'<div class="av-flag"><div class="t"><span class="pill {cls}">{label}</span>'
            f'{esc(r["name"])}<span class="id">{esc(r["id"])}</span></div>'
            f'<p class="n">{esc(r["note"])}{why}</p>'
            + (f'<div class="nums">{"".join(nums)}</div>' if nums else "")
            + f'<p class="a">Assertion at risk: {esc(r["assertion"])} · Threshold: {esc(r["threshold"])}</p></div>')
    n = sum(r["status"] == "flag" for r in flags)
    grade = ("rd", f"{n} flag{'s' if n != 1 else ''}") if n else ("g", "No flags")
    intro = f'<p class="av-note" style="margin:0 0 6px">Six ratio tests on revenue and earnings quality, latest year vs prior year. What\'s an assertion? {ASSERTION_TIP}</p>'
    return sl.section("av-flags", "risk", "Red-flag tests",
                      "Where revenue and earnings misstatements tend to hide", grade,
                      intro + "".join(rows), open_default=True)


def render_fluctuations(rows, fy_ends, materiality_amount):
    yrs = [year_label(e) for e in fy_ends] + ["", "", ""]
    head = (f"<tr><th>Line item</th><th>{yrs[0]}</th><th>{yrs[1]}</th><th>{yrs[2]}</th>"
            f"<th>{yrs[0]} vs {yrs[1]}</th><th>{yrs[1]} vs {yrs[2]}</th></tr>")
    body, whys = [], []
    for row in rows:
        v = row["values"]
        cells = "".join(f'<td class="nr">{NOT_REPORTED}</td>' if x is None else f"<td>{esc(money(x))}</td>"
                        for x in v[:3])

        def chg(c):
            if c["status"] == "not enough data":
                return '<td class="nr">—</td>'
            return f'<td class="chg{" fl" if c["flag"] else ""}">{esc(pct(c["pct"]))}</td>'
        calc = ' <span class="calc">(calculated)</span>' if row.get("derived") else ""
        tr = '<tr class="fl">' if row["flag"] else "<tr>"
        body.append(f'{tr}<td>{esc(row["label"])}{calc}</td>'
                    f'{cells}{chg(row["latest"])}{chg(row["earlier"])}</tr>')
        if row["flag"]:
            whys.append(f'<li><b>{esc(row["label"])}</b> ({esc(row["assertion"])}): {esc(row["why"])}</li>')
    n = sum(r["flag"] for r in rows)
    grade = ("a", f"{n} flagged") if n else ("g", "None flagged")
    mat = money(materiality_amount) if materiality_amount else "materiality"
    intro = (f'<p class="av-note" style="margin:0 0 10px">A row is flagged when it moved by at least '
             f'{esc(mat)} (materiality) <b>and</b> at least 10%. Auditors ask management to explain moves like these.</p>')
    table = f'<div class="av-scroll"><table class="av-table"><thead>{head}</thead><tbody>{"".join(body)}</tbody></table></div>'
    why_html = f'<ul class="av-why">{"".join(whys)}</ul>' if whys else ""
    return sl.section("av-flux", "growth", "Fluctuation analysis",
                      "Three years side by side, big moves highlighted", grade,
                      intro + table + why_html, open_default=True)


def render_ai_placeholder():
    body = ('<div id="audit-ai" class="av-ai"><p class="av-note"><span class="spin"></span>'
            'Reading the revenue recognition policy in the latest 10-K. This can take 20 seconds the first time.</p></div>')
    return sl.section("av-ai", "summary", "Revenue recognition (ASC 606)",
                      "What the company says about how it books revenue", None, body, open_default=True)


HOW_IT_WORKS = [
    "We pull three years of annual figures from the company's 10-K filings in SEC EDGAR, using the XBRL tags attached to each number.",
    "Materiality is set at 5% of pre-tax income (0.5% of revenue if profit is small or negative). It decides how big a difference has to be to matter.",
    "Fluctuation analysis flags any line that moved by at least materiality and at least 10% from the year before. Auditors ask for an explanation of each one.",
    "Six red-flag tests compare related numbers, such as receivables against sales and profit against cash. Each one names the assertion most at risk.",
    "An AI model reads the company's own revenue recognition policy and lists the ASC 606 judgment areas. Its quotes are checked against the filing text.",
]


def render_how_it_works():
    items = "".join(f"<li>{esc(x)}</li>" for x in HOW_IT_WORKS)
    body = (f'<div class="av-how"><ol>{items}</ol><p class="av-note">This is a simplified, educational '
            f'version of audit planning analytics. It is not an audit and not an audit opinion.</p></div>')
    return sl.section("av-how", "primer", "How this works", "The method in five lines", None, body,
                      open_default=True)


# ------------------------------------------------------------------ whole tab
NO_XBRL_MESSAGE = ("Audit View needs annual 10-K filings with XBRL data from the SEC. "
                   "This company doesn't have them (foreign companies file a 20-F instead, and "
                   "brand-new listings may not have filed a 10-K yet).")


def render_message(msg):
    return f'<div class="av-empty">{esc(msg)}</div>'


def render_audit_view(ticker, out):
    """Full tab HTML from audit_analytics.analyze() output."""
    if not out.get("ok"):
        return render_message(NO_XBRL_MESSAGE)
    banner = ('<div class="av-banner">Limited support for banks and insurers: their statements are built '
              'differently, so some tests don\'t apply.</div>') if out.get("limited_support") else ""
    return (render_header(out, ticker) + banner
            + render_materiality(out["materiality"])
            + render_red_flags(out["red_flags"])
            + render_fluctuations(out["fluctuations"], out.get("fy_ends") or [],
                                  out["materiality"].get("amount"))
            + render_ai_placeholder()
            + render_how_it_works())


def render_summary(payload):
    """Inner HTML of the AI section, from the /audit/summary payload."""
    if not payload.get("ok") or not payload.get("summary"):
        return '<p class="av-note">Summary unavailable.</p>'
    s = payload["summary"]
    parts = [f'<p class="lbl">{esc(payload.get("label") or "")}</p>',
             f'<p class="item">{esc(s["how_they_make_money"])}</p>']
    if s.get("judgment_areas"):
        parts.append("<h4>Judgment areas</h4>")
        for a in s["judgment_areas"]:
            q = f'<span class="q">“{esc(a["evidence"])}”</span>' if a.get("evidence") else ""
            parts.append(f'<div class="item"><b>{esc(a["area"])}.</b> {esc(a["plain_english"])}{q}</div>')
    if s.get("top_audit_risks"):
        parts.append("<h4>Where the audit risk is</h4>")
        for r in s["top_audit_risks"]:
            link = f'<span class="link">{esc(r["linked_flag"])}</span>' if r.get("linked_flag") else ""
            parts.append(f'<div class="item"><b>{esc(r["risk"])}</b>{link}<br>{esc(r["why"])}</div>')
    if s.get("not_disclosed"):
        parts.append("<h4>Not covered in the text we read</h4>")
        parts.append(f'<p class="item">{esc(", ".join(s["not_disclosed"]))}</p>')
    acc = payload.get("accession") or ""
    parts.append(f'<p class="av-note">Source: 10-K accession {esc(acc)} ({esc(payload.get("source") or "")}).</p>')
    return "".join(parts)
