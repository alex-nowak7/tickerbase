"""Play back saved SEC responses (recorded by scripts/fetch_fixtures.py)."""

import json
import pathlib

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def replay_http(ticker):
    folder = FIXTURES / ticker
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))

    async def get(url):
        if url not in manifest:
            raise AssertionError(f"test tried to fetch an unrecorded URL: {url}")
        name = manifest[url]
        if name is None:
            return 404, None
        return 200, (folder / name).read_text(encoding="utf-8")
    return get
