"""
Plain-text F1-F3 report straight from live SEC data (for sanity checks).

    .venv\\Scripts\\python.exe scripts\\audit_report.py AAPL WMT CAT
"""

import asyncio
import sys

from sec_local import aget

import audit_analytics as aa
import edgar_client as ec
from sec_tickers import TICKER_TO_CIK


def money(v):
    if v is None:
        return "n/r"
    a = abs(v)
    s = f"${a/1e9:,.1f}B" if a >= 1e9 else f"${a/1e6:,.0f}M"
    return "-" + s if v < 0 else s


def pct(p):
    return "n/m" if p is None else f"{p*100:+.1f}%"


def report(ticker, ds):
    out = aa.analyze(ds)
    lines = [f"\n{'=' * 78}", f"{ticker}  {ds.get('entity')}"]
    if not out["ok"]:
        return "\n".join(lines + [f"  No usable XBRL 10-K data ({out['reason']})."])
    fy = [e[:4] for e in out["fy_ends"]]   # year the fiscal year ends in
    lines.append(f"Fiscal years ending {', '.join(out['fy_ends'])}  |  Source: SEC EDGAR (XBRL)")
    if out["limited_support"]:
        lines.append("  ! Limited support for banks/insurers")

    m = out["materiality"]
    lines += ["", "F1  PLANNING MATERIALITY",
              f"  {money(m['amount'])}  = {m['benchmark_label']} ({money(m['benchmark_value'])})"]
    if m["reason"]:
        lines.append(f"  {m['reason']}")

    lines += ["", "F3  RED FLAGS (latest year vs prior)"]
    for r in out["red_flags"]:
        tag = {"flag": "FLAG ", "ok": "ok   ", "not enough data": "n/a  "}[r["status"]]
        lines.append(f"  [{tag}] {r['id']} {r['name']}")
        lines.append(f"          {r['note']}")
        nums = {k: v for k, v in r["numbers"].items() if k != "changes"}
        if nums:
            lines.append("          " + ", ".join(
                f"{k}={money(v) if isinstance(v, (int, float)) and abs(v) > 1e5 else v}" for k, v in nums.items()))
        for c in r["numbers"].get("changes", [])[:5]:
            lines.append(f"          {c['item']} FY ending {c['year_end']}: originally {money(c['originally'])}, "
                         f"now {money(c['now'])} ({c['pct']}%)")
        if r["status"] == "flag":
            lines.append(f"          Why: {r['why']}  Assertion: {r['assertion']}")

    lines += ["", "F2  FLUCTUATIONS  (* = flagged: |change| >= materiality AND >= 10%; n/r = not reported)",
              f"  {'':22s}{fy[0]:>11s}{fy[1]:>11s}{fy[2]:>11s}   {'latest vs prior':>16s}   {'prior vs 2y':>12s}"]
    for row in out["fluctuations"]:
        v = row["values"]
        lt, er = row["latest"], row["earlier"]
        label = row["label"] + (" (calc)" if row["derived"] else "")
        lines.append(f"  {label:22s}" + "".join(f"{money(x):>11s}" for x in v) +
                     f"   {pct(lt['pct']):>9s}{'*' if lt['flag'] else ' ':>2s}     {pct(er['pct']):>9s}{'*' if er['flag'] else ' '}")
    for row in out["fluctuations"]:
        if row["flag"]:
            lines.append(f"    * {row['label']}: {row['why']} (assertion: {row['assertion']})")
    return "\n".join(lines)


async def main(tickers):
    for t in tickers:
        t = t.upper()
        ds = await ec.load_financials(ec.Edgar(aget), TICKER_TO_CIK[t])
        print(report(t, ds))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or ["AAPL", "WMT", "CAT"]))
