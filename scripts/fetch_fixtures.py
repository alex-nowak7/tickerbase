"""
Record real SEC responses for tests, so unit tests never call the SEC.

Runs the same loaders the Worker uses, saves every response under
tests/fixtures/<TICKER>/, and writes manifest.json mapping URL -> file
(or null for a 404). tests/replay.py plays them back.

    .venv\\Scripts\\python.exe scripts\\fetch_fixtures.py AAPL WMT
"""

import asyncio
import hashlib
import json
import sys

from sec_local import REPO, aget

import edgar_client as ec
from sec_tickers import TICKER_TO_CIK


def recorder(folder, manifest):
    async def get(url):
        status, text = await aget(url)
        if status == 200 and text is not None:
            name = url.rsplit("/", 1)[-1]
            if name in ("FilingSummary.xml",) or name.startswith("R"):
                name = "archive_" + name
            path = folder / name
            path.write_text(text, encoding="utf-8")
            manifest[url] = name
        else:
            manifest[url] = None
        return status, text
    return get


async def record(ticker):
    cik = TICKER_TO_CIK[ticker]
    folder = REPO / "tests" / "fixtures" / ticker
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {}
    edgar = ec.Edgar(recorder(folder, manifest))
    ds = await ec.load_financials(edgar, cik)
    pol = await ec.load_policy_text(edgar, cik)
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")

    print(f"\n=== {ticker}  {ds.get('entity')}  CIK {cik}  ({edgar.requests} SEC requests)")
    print("profile:", ds.get("profile"))
    print("latest 10-K:", ds.get("latest_10k"))
    if ds.get("ok"):
        print("fiscal year-ends:", ds["fy_ends"])
        for key, it in ds["items"].items():
            vals = ["Not reported" if v is None else f"{v/1e6:,.0f}" for v in it["values"]]
            print(f"  {it['label']:22s} {str(it['tag'])[:48]:48s} {vals}")
        print("changed prior-year figures:", ds["restatements"] or "none")
    else:
        print("NOT OK:", ds.get("reason"))
    print("policy text:", pol.get("source"), "|", len(pol.get("text") or ""), "chars | pages", pol.get("pages"))
    print("  starts:", (pol.get("text") or "")[:300].replace("\n", " "))


async def main(tickers):
    for t in tickers:
        await record(t.upper())


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or ["AAPL", "WMT"]))
