from wire3_gtm.links import check_links, classify


def fake(results):
    return lambda url: results[url] if not isinstance(results[url], Exception) else (_ for _ in ()).throw(results[url])


def test_only_evidence_that_a_page_is_gone_counts_as_broken():
    assert classify({"is_valid": True, "status_code": 200}) == "ok"
    assert classify({"is_valid": False, "status_code": 404}) == "broken"
    assert classify({"is_valid": False, "status_code": 410}) == "broken"
    assert classify({"is_valid": False, "status_code": None, "error": "[Errno 8] nodename nor servname provided"}) == "broken"
    for s in (403, 429, 999, 405):
        assert classify({"is_valid": False, "status_code": s}) == "blocked"


def test_timeouts_and_server_errors_are_unverified_not_broken():
    """Regression: a live run's 'broken' links were read timeouts (HEAD status None); two of the pages
    answered a normal GET with 403, so they were alive."""
    assert classify({"is_valid": False, "status_code": None, "error": "The read operation timed out"}) == "unverified"
    assert classify({"is_valid": False, "status_code": 503}) == "unverified"
    assert classify({"is_valid": False, "status_code": None, "error": "Connection reset by peer"}) == "unverified"


def test_check_links_counts_each_class_and_never_fails_the_run():
    urls = ["https://a.example", "https://b.example", "https://c.example", "https://d.example", "https://e.example", "not a url", "https://a.example"]
    out = check_links(urls, fake({
        "https://a.example": {"is_valid": True, "status_code": 200},
        "https://b.example": {"is_valid": False, "status_code": 404},
        "https://c.example": {"is_valid": False, "status_code": 429},
        "https://d.example": RuntimeError("validator crashed"),
        "https://e.example": {"is_valid": False, "status_code": None, "error": "The read operation timed out"},
    }))
    assert out["urls_total"] == 6  # duplicates collapsed
    assert (out["ok"], out["broken_url_count"], out["blocked"], out["unverified"], out["unchecked"], out["invalid_url_count"]) == (1, 1, 1, 1, 1, 1)
    assert out["broken"][0]["url"] == "https://b.example" and out["malformed"] == ["not a url"]
