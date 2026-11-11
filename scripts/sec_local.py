"""
Local-only helpers for talking to SEC EDGAR from your laptop (not the Worker).

The SEC asks every automated client to identify itself with a User-Agent that
includes a contact email. On the Worker that comes from the SEC_USER_AGENT
secret; locally it comes from the gitignored .dev.vars file in the repo root.
"""

import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))   # lets scripts import the app's modules

MIN_INTERVAL = 0.15   # seconds between requests -> under 7/sec, SEC allows 10


def user_agent():
    path = REPO / ".dev.vars"
    if path.exists():
        m = re.search(r'^SEC_USER_AGENT\s*=\s*"?([^"\r\n]+)"?\s*$',
                      path.read_text(encoding="utf-8"), re.M)
        if m:
            return m.group(1).strip()
    sys.exit("SEC_USER_AGENT not found in .dev.vars")


_last = [0.0]


def get(url):
    """Polite GET. Returns (status, text). Never raises on HTTP errors."""
    wait = MIN_INTERVAL - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": user_agent(),
                                               "Accept-Encoding": "identity"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, None


async def aget(url):
    """Async wrapper so local scripts can drive the same async EDGAR code the
    Worker uses."""
    return get(url)


def get_json(url):
    status, text = get(url)
    if status != 200:
        raise RuntimeError(f"{url} -> HTTP {status}")
    return json.loads(text)
