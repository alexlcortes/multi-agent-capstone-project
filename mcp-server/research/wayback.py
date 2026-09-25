"""archived_page: the prices on an archived copy of a public web page, from the Internet
Archive's Wayback Machine, shaped like search results so each becomes cited evidence.

Why archived copies: provider pricing pages change and some block automated readers, while a
Wayback snapshot is public, dated and never changes, so it is a stable citation. Only the text
the page shipped with is read: prices a page loads later by script are not in the snapshot,
which is why local city pages (server-rendered) work and national plan pages often do not.

Free, no key. The archive asks for gentle use, so requests are spaced (REQUEST_GAP_S) and
results are cached like search results.
"""

from __future__ import annotations

import gzip
import html
import re
import time
from datetime import datetime, timezone

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from . import cache

AVAILABLE = "https://archive.org/wayback/available"
CDX = "https://web.archive.org/cdx/search/cdx"
HEADERS = {"User-Agent": "wire3-gtm-research/1.0 (academic capstone; Wayback Machine API)"}
REQUEST_GAP_S = 1.0
MAX_BLOCKS = 8

PRICE = re.compile(r"\$\s?\d{1,4}(?:\.\d{2})?")
# What makes a price block about home-internet pricing (speed tiers, promo vs. regular price,
# price locks) rather than the TV, phone, streaming and device offers on the same page.
PLAN = re.compile(r"\b\d+\s?(?:mbps|gig|gbps)\b|\bgig\b", re.I)
TERMS = re.compile(r"everyday|special offer|regular (?:rate|price)|standard (?:rate|price)|after (?:\d+|one|two|the first)|"
                   r"for (?:one|two|three|\d+) (?:year|month)s?|price (?:guarantee|lock)|won.t change|starting at|"
                   r"plans? start|auto ?pay|was \$", re.I)
OTHER = re.compile(r"\b(tv|streaming|netflix|peacock|hulu|disney|espn|home phone|security|cameras?|airpods|"
                   r"mobile line|prepaid card|rebate)\b", re.I)
MONTHLY = re.compile(r"/\s?m\s?o\b|per month|a month|monthly", re.I)  # "/m | o": some pages split "/mo" across elements
_last_request = [0.0]


def _get(url: str, params: dict | None = None) -> httpx.Response:
    wait = REQUEST_GAP_S - (time.monotonic() - _last_request[0])
    if wait > 0:
        time.sleep(wait)
    _last_request[0] = time.monotonic()
    try:
        return httpx.get(url, params=params, headers=HEADERS, timeout=40.0, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise ToolError(f"Wayback Machine request failed: {exc}") from None


def _variants(url: str) -> list[str]:
    """The archive often holds a page under only one of 'path' and 'path/'."""
    return [url, url[:-1] if url.endswith("/") else url + "/"]


def find_snapshot(url: str, before: str) -> tuple[str, str] | None:
    """(timestamp, original url) of the newest archived copy at or before `before` (YYYYMMDD)."""
    for candidate in _variants(url):
        r = _get(AVAILABLE, {"url": candidate, "timestamp": before})
        if r.status_code == 200:
            snap = (r.json().get("archived_snapshots") or {}).get("closest")
            if snap and snap.get("status", "200") == "200" and snap["timestamp"][:8] <= before:
                return snap["timestamp"], candidate
    for candidate in _variants(url):  # the availability API misses some captures the index has
        r = _get(CDX, {"url": candidate, "output": "json", "to": before, "filter": "statuscode:200",
                       "fl": "timestamp,original", "limit": "-1"})
        rows = r.json() if r.status_code == 200 and r.text.strip() else []
        if len(rows) > 1:
            return rows[-1][0], candidate
    return None


def page_text(raw: bytes) -> list[str]:
    """Visible text lines of an HTML page (scripts, styles and markup removed)."""
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    page = raw.decode("utf-8", "replace")
    title = re.search(r"(?is)<title[^>]*>(.*?)</title>", page)
    page = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", page)
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in html.unescape(re.sub(r"(?s)<[^>]+>", "\n", page)).splitlines()]
    lines = [ln for ln in lines if ln]
    return ([f"TITLE: {html.unescape(title.group(1)).strip()}"] if title else []) + lines


def _clean(text: str) -> str:
    text = re.sub(r"(\$\s?\d[\d.,]*) \| \1\b", r"\1", text)  # "$30 | $30" shown twice on some pages
    text = re.sub(r"/m \| o\b", "/mo", text)
    return re.sub(r'"?\s*data-[\w-]+="[^"]*"', "", text)  # attribute text some pages leave in the markup


def _score(text: str) -> int:
    return (3 * bool(PLAN.search(text)) + 2 * bool(TERMS.search(text)) + bool(re.search(r"internet", text, re.I))
            - 3 * len({m.lower() for m in OTHER.findall(text)}))


def price_blocks(lines: list[str]) -> list[str]:
    """Home-internet monthly prices with the lines around them (plan name, 'Everyday pricing', fine print).
    Each price's snippet is scored on its own, so a TV or security offer next to it is dropped rather
    than merged in; the snippets that pass are then merged where they overlap. One-time prices
    (a '$129 value') are not monthly and are skipped."""
    body = [ln for ln in lines if not ln.startswith("TITLE: ")]
    kept = []
    for i, line in enumerate(body):
        if PRICE.search(line) and MONTHLY.search(" ".join(body[i:i + 3])) and not OTHER.search(line):
            a, b = max(0, i - 2), min(len(body), i + 4)
            score = _score(_clean(" | ".join(body[a:b])))
            if score >= 2:
                kept.append([a, b, score])
    merged: list[list[int]] = []
    for a, b, score in kept:
        if merged and a <= merged[-1][1]:
            merged[-1][1], merged[-1][2] = max(merged[-1][1], b), max(merged[-1][2], score)
        else:
            merged.append([a, b, score])
    scored, seen = [], set()
    for a, b, score in merged:
        text = _clean(" | ".join(body[a:b]))
        key = re.sub(r"\W+", "", text.lower())[:160]  # near-duplicates differ only in their tails
        if key not in seen:
            seen.add(key)
            scored.append((score, a, text[:480]))
    best = sorted(scored, key=lambda t: (-t[0], t[1]))[:MAX_BLOCKS]
    return [text for _, _, text in sorted(best, key=lambda t: t[1])]  # back in page order


def archived_page(url: str, before: str | None = None) -> list[dict]:
    """Monthly prices, with their surrounding plan names and fine print, from the newest Wayback
    Machine snapshot of a public page taken on or before `before` (YYYY-MM-DD; default today).
    Each result cites the snapshot itself (web.archive.org/web/<timestamp>/<url>) and its date."""
    url = url.strip()
    if not re.match(r"^https?://", url):
        raise ToolError(f"url must start with http:// or https://, got {url!r}")
    cutoff = re.sub(r"\D", "", before or datetime.now(timezone.utc).strftime("%Y%m%d"))[:8]
    if len(cutoff) != 8:
        raise ToolError(f"before must be a date like 2026-09-01, got {before!r}")
    cached = cache.get("wayback", f"{cutoff}|{url}", 0)
    if cached is not None:
        return cached
    found = find_snapshot(url, cutoff)
    if found is None:
        raise ToolError(f"the Wayback Machine has no snapshot of {url} on or before {cutoff}")
    ts, original = found
    r = _get(f"https://web.archive.org/web/{ts}id_/{original}")
    if r.status_code != 200:
        raise ToolError(f"Wayback Machine returned HTTP {r.status_code} for the {ts} snapshot of {url}")
    lines = page_text(r.content)
    title = next((ln[7:] for ln in lines if ln.startswith("TITLE: ")), original)
    date = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
    retrieved = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    results = [
        {
            "source_title": f"{title} (Wayback Machine snapshot, {date})",
            "source_url": f"https://web.archive.org/web/{ts}/{original}",
            "excerpt": f"Archived {date}: {block}",
            "publication_date": date,
            "retrieval_timestamp": retrieved,
        }
        for block in price_blocks(lines)
    ]
    cache.put("wayback", f"{cutoff}|{url}", 0, results, retrieved)
    return [{**res, "from_cache": False} for res in results]
