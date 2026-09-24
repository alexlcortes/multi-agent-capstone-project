from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from research import census
from research.census import census_profile

KEY = "test-census-key-0123456789"
PLACES = [["NAME", "state", "place"], ["Leesburg city, Florida", "12", "39875"],
          ["Mount Dora city, Florida", "12", "47050"], ["Leesburg CDP, Florida", "12", "39900"]]
COUNTIES = [["NAME", "state", "county"], ["Lake County, Florida", "12", "069"]]
DATA = {"NAME": "Leesburg city, Florida", "B19013_001E": "52880", "B17001_001E": "22000", "B17001_002E": "3608",
        "B25003_001E": "12833", "B25003_003E": "4800", "B25024_001E": "15000", "B25024_004E": "500",
        "B25024_005E": "900", "B25024_006E": "1000", "B25024_007E": "800", "B25024_008E": "700",
        "B25024_009E": "800", "B25024_010E": "1350", "B01002_001E": "46.2", "B28002_001E": "12833",
        "B28002_004E": "11665", "B28002_013E": "436", "C16001_001E": "25000", "C16001_003E": "3925"}


def _response(payload, status=200):
    return httpx.Response(status, text=json.dumps(payload), request=httpx.Request("GET", census.DATASET))


def fake_census(params, **_):
    assert params["key"] == KEY
    if params["get"] == "NAME":
        return _response(COUNTIES if params["for"] == "county:*" else PLACES)
    return _response([list(DATA), list(DATA.values())])


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("CENSUS_API_KEY", KEY)
    with patch("research.census.httpx.get", side_effect=lambda url, params, **kw: fake_census(params, **kw)) as get:
        yield get


def test_city_profile_is_four_cited_topics(api):
    results = census_profile("Leesburg city, FL")
    assert [r["source_title"].split(": ")[1].split(" (")[0] for r in results] == [
        "income and poverty", "housing", "age and language", "internet access"]
    assert {r["source_url"] for r in results} == {"https://data.census.gov/profile?g=160XX00US1239875"}
    text = " ".join(r["excerpt"] for r in results)
    for figure in ("$52,880", "16.4% of people below the poverty level", "37.4% renter-occupied", "9.0% mobile homes",
                   "median age 46.2", "15.7% of people aged 5+ speak Spanish", "90.9% of households have a broadband"):
        assert figure in text


def test_short_city_name_resolves_to_the_city_not_a_cdp_with_the_same_exact_base(api):
    PLACES.append(["Mount Dora CDP, Florida", "12", "47099"])
    try:
        with pytest.raises(ToolError, match="matched 2"):
            census_profile("Mount Dora, FL")
        assert census_profile("Mount Dora city, FL")[0]["source_url"].endswith("1247050")
    finally:
        PLACES.pop()


def test_zip_and_county(api):
    assert census_profile("ZIP 34788")[0]["source_url"].endswith("860XX00US34788")
    assert census_profile("Lake County, FL")[0]["source_url"].endswith("050XX00US12069")


def test_the_key_never_appears_in_results_or_errors(api):
    assert KEY not in json.dumps(census_profile("ZIP 32757"))
    api.side_effect = lambda url, params, **kw: _response({"error": f"bad key {params['key']}"}, 400)
    with pytest.raises(ToolError) as err:
        census_profile("ZIP 34748")
    assert KEY not in str(err.value) and "***" in str(err.value)


def test_a_rejected_key_is_a_clear_error(api):
    api.side_effect = lambda url, params, **kw: httpx.Response(302, request=httpx.Request("GET", url))
    with pytest.raises(ToolError, match="rejected the key"):
        census_profile("ZIP 34748")


def test_missing_key_and_bad_geography(api, monkeypatch):
    with pytest.raises(ToolError, match="must look like"):
        census_profile("Leesburg")
    with pytest.raises(ToolError, match="must not be empty"):
        census_profile("  ")
    monkeypatch.delenv("CENSUS_API_KEY")
    with pytest.raises(ToolError, match="CENSUS_API_KEY is not set"):
        census_profile("ZIP 11111")


def test_unavailable_figures_are_marked_not_invented(api):
    DATA["B19013_001E"] = "-666666666"
    try:
        assert "median household income not available" in census_profile("ZIP 32757")[0]["excerpt"]
    finally:
        DATA["B19013_001E"] = "52880"


def test_results_are_cached_without_a_second_request(api):
    census_profile("ZIP 32757")
    calls = api.call_count
    assert census_profile("ZIP 32757")[0]["from_cache"] is True and api.call_count == calls
