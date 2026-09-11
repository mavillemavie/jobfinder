import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from jobfinder import paths
from jobfinder.config import load_profile
from jobfinder.discovery.base import LocationQuery, SearchProfile, SourceError
from jobfinder.discovery.fetch import HttpClient, RateLimited
from jobfinder.discovery.sources.adzuna import AdzunaSource

FIX = json.loads((Path(__file__).parent.parent / "fixtures" / "adzuna" / "search.json").read_text())


def test_parse() -> None:
    q = LocationQuery("montreal", "CA", "Montreal, QC")
    raws = AdzunaSource(HttpClient(), "id", "key").parse(FIX, q)
    assert len(raws) == 2
    r = raws[0]
    assert r.source == "adzuna"
    assert r.source_id == "5001"
    assert r.title == "Senior Data Analyst"
    assert r.company_name == "Acme Logistics Inc."
    assert r.location_raw == "Montreal, Quebec"
    assert r.country_hint == "CA"
    assert r.location_key == "montreal"
    assert r.description_complete is False
    assert r.salary_raw == "70000-85000"
    assert r.posted_at == datetime(2026, 9, 1, 14, 22, 10, tzinfo=UTC)


@respx.mock
def test_fetch_builds_requests_per_keyword_and_location() -> None:
    route = respx.get(
        url__regex=r"https://api\.adzuna\.com/v1/api/jobs/(ca|gb)/search/1.*"
    ).mock(return_value=httpx.Response(200, json=FIX))
    profile_yaml = paths.repo_root() / "tests" / "fixtures" / "profile.yaml"
    profile = SearchProfile.from_profile(load_profile(profile_yaml))
    raws = AdzunaSource(HttpClient(), "id", "key").fetch(profile, datetime.now(UTC))
    # 5 country-bound queries (canada-any, edmonton, montreal, vancouver, uk) × 4 keywords
    assert route.call_count == 30  # 6 keywords (2 per cluster × 3 clusters) × 5 country queries
    first = route.calls[0].request.url
    assert first.params["what"] == "data analyst"
    assert first.params["max_days_old"] == "21"
    assert first.params["app_id"] == "id"
    assert first.params["results_per_page"] == "50"
    # title_only/distance are unconfirmed on Adzuna's docs (api-notes.md 2026-09-04);
    # sort_by's only documented value is "salary" (not the draft's "date") so we don't
    # send it either.
    assert "title_only" not in first.params
    assert "distance" not in first.params
    assert "sort_by" not in first.params
    assert len(raws) == 60


def _profile() -> SearchProfile:
    fixture = paths.repo_root() / "tests" / "fixtures" / "profile.yaml"
    return SearchProfile.from_profile(load_profile(fixture))


@respx.mock
def test_fetch_keeps_the_other_queries_when_one_keeps_failing(monkeypatch) -> None:
    """2026-09-06 06:00: one 503 query (after 3 attempts) threw away the whole Adzuna run,
    including the 4 queries that had succeeded. A failed query is skipped, not fatal."""
    monkeypatch.setattr("jobfinder.discovery.fetch.time.sleep", lambda _s: None)

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.params["what"] == "people analytics":
            return httpx.Response(503)
        return httpx.Response(200, json=FIX)

    route = respx.get(url__regex=r"https://api\.adzuna\.com/.*").mock(side_effect=respond)
    raws = AdzunaSource(HttpClient(), "id", "key").fetch(_profile(), datetime.now(UTC))
    assert len(raws) == 50  # 25 good queries × 2 fixture items; 5 failed queries dropped
    assert route.call_count == 25 + 5 * 3  # each failing query still gets its 3 attempts


@respx.mock
def test_fetch_raises_when_every_query_fails(monkeypatch) -> None:
    monkeypatch.setattr("jobfinder.discovery.fetch.time.sleep", lambda _s: None)
    respx.get(url__regex=r"https://api\.adzuna\.com/.*").mock(return_value=httpx.Response(503))
    with pytest.raises(SourceError):
        AdzunaSource(HttpClient(), "id", "key").fetch(_profile(), datetime.now(UTC))


@respx.mock
def test_fetch_still_propagates_rate_limits(monkeypatch) -> None:
    respx.get(url__regex=r"https://api\.adzuna\.com/.*").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "60"})
    )
    with pytest.raises(RateLimited):
        AdzunaSource(HttpClient(), "id", "key").fetch(_profile(), datetime.now(UTC))
