from __future__ import annotations

import gzip
import json
from unittest.mock import patch

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from research import wayback
from research.wayback import archived_page, price_blocks

URL = "https://www.xfinity.com/local/fl/mount-dora"
PAGE = """<html><head><title>Xfinity Internet in Mount Dora, FL</title><script>var p="$99/mo";</script></head><body>
<div>Watch what you love with NOW TV</div><div>NOW TV gives you live TV for $20/mo.</div>
<h2>Home Internet Pricing in Mount Dora</h2><p>Speed: 300 Mbps</p>
<li>$45/mo - Special offer pricing</li><li>$75/mo - Everyday pricing</li>
<p>FREE installation - $129 value</p>
<p>Spectrum-style tier</p><p>1 Gig Internet</p><p>$50</p><p>$50</p><p>/m</p><p>o</p><p>for 1 year</p>
</body></html>"""


def respond(payload=None, status=200, body=None):
    content = body if body is not None else json.dumps(payload).encode()
    return httpx.Response(status, content=content, request=httpx.Request("GET", "https://archive.org"))


class Archive:
    """Routes the three Wayback endpoints; records every request."""

    def __init__(self, available=None, cdx=None, page=PAGE.encode(), status=200):
        self.available, self.cdx, self.page, self.status, self.calls = available or {}, cdx or {}, page, status, []

    def __call__(self, url, params=None, **_):
        self.calls.append((url, params))
        if url == wayback.AVAILABLE:
            snap = self.available.get(params["url"])
            return respond({"archived_snapshots": {"closest": snap} if snap else {}})
        if url == wayback.CDX:
            return respond(self.cdx.get(params["url"], []))
        return respond(status=self.status, body=self.page)


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(wayback, "REQUEST_GAP_S", 0)


def run(archive, url=URL, before="2026-09-01"):
    with patch("research.wayback.httpx.get", side_effect=archive):
        return archived_page(url, before)


def test_internet_prices_are_returned_with_the_snapshot_as_the_citation():
    archive = Archive(available={URL: {"timestamp": "20260518232607", "status": "200"}})
    results = run(archive)
    assert {r["source_url"] for r in results} == {f"https://web.archive.org/web/20260518232607/{URL}"}
    assert {r["publication_date"] for r in results} == {"2026-05-18"}
    assert results[0]["source_title"] == "Xfinity Internet in Mount Dora, FL (Wayback Machine snapshot, 2026-05-18)"
    text = " ".join(r["excerpt"] for r in results)
    assert "$45/mo - Special offer pricing" in text and "$75/mo - Everyday pricing" in text
    assert "1 Gig Internet | $50 | /mo | for 1 year" in text  # split "/m" "o" rejoined, doubled price collapsed
    assert "NOW TV" not in text and "$99" not in text  # TV offer ranked out; script text never read
    assert archive.calls[-1][0] == f"https://web.archive.org/web/20260518232607id_/{URL}"  # the page as captured


def test_the_trailing_slash_variant_and_the_cdx_index_are_tried():
    archive = Archive(cdx={URL + "/": [["timestamp", "original"], ["20260110000000", URL + "/"],
                                       ["20260203201308", URL + "/"]]})
    results = run(archive)
    assert results[0]["source_url"] == f"https://web.archive.org/web/20260203201308/{URL}/"
    assert [c[1]["url"] for c in archive.calls[:4]] == [URL, URL + "/", URL, URL + "/"]


def test_a_snapshot_after_the_cutoff_is_not_used():
    archive = Archive(available={URL: {"timestamp": "20260920000000", "status": "200"}})
    with pytest.raises(ToolError, match="no snapshot"):
        run(archive, before="2026-09-01")


def test_gzipped_snapshots_are_read():
    archive = Archive(available={URL: {"timestamp": "20260518232607", "status": "200"}}, page=gzip.compress(PAGE.encode()))
    assert any("Everyday pricing" in r["excerpt"] for r in run(archive))


def test_errors_are_clear():
    with pytest.raises(ToolError, match="must start with http"):
        run(Archive(), url="xfinity.com")
    with pytest.raises(ToolError, match="before must be a date"):
        run(Archive(), before="soon")
    archive = Archive(available={URL: {"timestamp": "20260518232607", "status": "200"}}, status=503)
    with pytest.raises(ToolError, match="HTTP 503"):
        run(archive)


def test_results_are_cached():
    archive = Archive(available={URL: {"timestamp": "20260518232607", "status": "200"}})
    run(archive)
    n = len(archive.calls)
    assert run(archive)[0]["from_cache"] is True and len(archive.calls) == n


def test_price_blocks_skip_one_time_prices_and_non_internet_offers():
    lines = ["Home security from $10/mo with cameras", "FREE installation - $129 value",
             "Fiber Internet", "Starting at", "$75", "/mo", "Speeds up to 940Mbps"]
    blocks = price_blocks(lines)
    assert len(blocks) == 1 and "$75 | /mo | Speeds up to 940Mbps" in blocks[0] and "security" not in blocks[0]
