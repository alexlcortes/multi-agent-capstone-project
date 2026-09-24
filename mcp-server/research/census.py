"""census_profile: U.S. Census ACS 5-year figures for one geography, shaped like a
search result so the pipeline turns it into cited evidence like any other source.

Not a SearchProvider: this is one fixed public dataset, queried by geography
rather than by free text. Needs CENSUS_API_KEY (free, api.census.gov/data/key_signup.html).
The key goes only into the request to api.census.gov: never into a returned URL,
an excerpt, the cache or an error message. Each result links to data.census.gov,
the public page for the same figures.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from . import cache

ACS_YEAR = "2024"  # the 2020-2024 5-year release
DATASET = f"https://api.census.gov/data/{ACS_YEAR}/acs/acs5"
LABEL = "U.S. Census Bureau, American Community Survey 2020-2024 5-year estimates"

STATE_FIPS = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09", "DE": "10", "DC": "11",
    "FL": "12", "GA": "13", "HI": "15", "ID": "16", "IL": "17", "IN": "18", "IA": "19", "KS": "20", "KY": "21",
    "LA": "22", "ME": "23", "MD": "24", "MA": "25", "MI": "26", "MN": "27", "MS": "28", "MO": "29", "MT": "30",
    "NE": "31", "NV": "32", "NH": "33", "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38", "OH": "39",
    "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46", "TN": "47", "TX": "48", "UT": "49",
    "VT": "50", "VA": "51", "WA": "53", "WV": "54", "WI": "55", "WY": "56",
}

VARIABLES = [
    "B19013_001E",  # median household income
    "B17001_001E", "B17001_002E",  # poverty universe, below poverty
    "B25003_001E", "B25003_003E",  # occupied housing units, renter-occupied
    "B25024_001E", *[f"B25024_{n:03d}E" for n in range(4, 11)],  # units in structure: 2+ units ... mobile home
    "B01002_001E",  # median age
    "B28002_001E", "B28002_004E", "B28002_013E",  # households, broadband of any type, no internet access
    "C16001_001E", "C16001_003E",  # population 5+, speaks Spanish at home
]

_ZIP = re.compile(r"^(?:zip|zcta)\s*(\d{5})$", re.I)
_NAMED = re.compile(r"^(.+?),\s*([A-Za-z]{2})$")


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _key() -> str:
    key = os.environ.get("CENSUS_API_KEY", "").strip()
    if not key:
        raise ToolError("census is not configured: CENSUS_API_KEY is not set")
    return key


def _get(params: dict, key: str) -> list[dict]:
    """One ACS request -> rows as dicts. Every error message has the key removed."""
    try:
        response = httpx.get(DATASET, params={**params, "key": key}, timeout=30.0, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise ToolError(f"census request failed: {str(exc).replace(key, '***')}") from None
    if response.status_code in (301, 302, 303, 307, 308):  # the API redirects a missing or invalid key
        raise ToolError("census API rejected the key (redirected to its key error page)")
    if response.status_code >= 400:
        raise ToolError(f"census API returned HTTP {response.status_code}: {response.text[:200].replace(key, '***')}")
    try:
        rows = response.json()
    except ValueError:
        raise ToolError("census API returned a non-JSON response (check CENSUS_API_KEY)") from None
    return [dict(zip(rows[0], row)) for row in rows[1:]]


def _resolve(geography: str, key: str) -> tuple[dict, str, str]:
    """'ZIP 34748' | 'Lake County, FL' | 'Leesburg city, FL' | 'Leesburg, FL'
    -> (query params, Census NAME, data.census.gov geo id)."""
    if m := _ZIP.match(geography):
        z = m.group(1)
        return {"for": f"zip code tabulation area:{z}"}, f"ZIP Code Tabulation Area {z}", f"860XX00US{z}"
    m = _NAMED.match(geography)
    if not m or m.group(2).upper() not in STATE_FIPS:
        raise ToolError(f"geography must look like 'ZIP 32757', 'Lake County, FL' or 'Leesburg city, FL', got {geography!r}")
    name, state = m.group(1).strip(), STATE_FIPS[m.group(2).upper()]
    level = "county" if name.lower().endswith(" county") else "place"
    rows = _get({"get": "NAME", "for": f"{level}:*", "in": f"state:{state}"}, key)
    base = name.lower()
    exact = [r for r in rows if r["NAME"].split(",")[0].lower() == base]
    # "Leesburg" matches "Leesburg city", "Leesburg town" or "Leesburg CDP"
    loose = [r for r in rows if re.fullmatch(rf"{re.escape(base)} (city|town|village|cdp|municipality)",
                                             r["NAME"].split(",")[0].lower())]
    hits = exact or loose
    if len(hits) != 1:
        found = ", ".join(r["NAME"] for r in hits) or "none"
        raise ToolError(f"geography {geography!r} matched {len(hits)} Census {level}s ({found}); be more specific")
    r = hits[0]
    if level == "county":
        return {"for": f"county:{r['county']}", "in": f"state:{state}"}, r["NAME"], f"050XX00US{state}{r['county']}"
    return {"for": f"place:{r['place']}", "in": f"state:{state}"}, r["NAME"], f"160XX00US{state}{r['place']}"


def _num(row: dict, var: str) -> float | None:
    v = row.get(var)
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if v < 0 else v  # the API codes "not available" as large negative numbers


def _pct(part, whole) -> str:
    return "not available" if part is None or not whole else f"{100 * part / whole:.1f}%"


def _money(v) -> str:
    return "not available" if v is None else f"${v:,.0f}"


def profile_results(row: dict, name: str, geo_id: str, retrieved: str) -> list[dict]:
    """Four search-shaped results per geography, one topic each, so each becomes a focused evidence claim."""
    def n(var):
        return _num(row, var)

    units, households, age = n("B25024_001E"), n("B25003_001E"), n("B01002_001E")
    multi = sum(n(f"B25024_{i:03d}E") or 0 for i in range(4, 10)) if units else None
    topics = {
        "income and poverty": (
            f"median household income {_money(n('B19013_001E'))}; "
            f"{_pct(n('B17001_002E'), n('B17001_001E'))} of people below the poverty level"),
        "housing": (
            f"{'not available' if households is None else f'{households:,.0f}'} households; "
            f"{_pct(n('B25003_003E'), households)} renter-occupied; "
            f"{_pct(multi, units)} of housing units in buildings with 2 or more units; "
            f"{_pct(n('B25024_010E'), units)} mobile homes"),
        "age and language": (
            f"median age {'not available' if age is None else f'{age:g}'}; "
            f"{_pct(n('C16001_003E'), n('C16001_001E'))} of people aged 5+ speak Spanish at home"),
        "internet access": (
            f"{_pct(n('B28002_004E'), n('B28002_001E'))} of households have a broadband subscription of any type; "
            f"{_pct(n('B28002_013E'), n('B28002_001E'))} have no internet access"),
    }
    return [
        {
            "source_title": f"{name}: {topic} ({LABEL})",
            "source_url": f"https://data.census.gov/profile?g={geo_id}",
            "excerpt": f"{name} ({LABEL}): {text}.",
            "publication_date": None,
            "retrieval_timestamp": retrieved,
        }
        for topic, text in topics.items()
    ]


def census_profile(geography: str) -> list[dict]:
    """Income, poverty, housing, age, language and internet-access figures for one U.S.
    geography from the Census ACS 5-year estimates. geography is 'ZIP 32757',
    'Lake County, FL', or a city/town like 'Leesburg city, FL' (or 'Leesburg, FL')."""
    geography = geography.strip()
    if not geography:
        raise ToolError("geography must not be empty")
    cached = cache.get("census", f"{ACS_YEAR}|{geography}", 0)
    if cached is not None:
        return cached
    key = _key()
    params, name, geo_id = _resolve(geography, key)
    rows = _get({"get": "NAME," + ",".join(VARIABLES), **params}, key)
    if not rows:
        raise ToolError(f"no ACS {ACS_YEAR} 5-year data for {geography!r}")
    retrieved = _timestamp()
    results = profile_results(rows[0], rows[0].get("NAME") or name, geo_id, retrieved)
    cache.put("census", f"{ACS_YEAR}|{geography}", 0, results, retrieved)
    return [{**r, "from_cache": False} for r in results]
