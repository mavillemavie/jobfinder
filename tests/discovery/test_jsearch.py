import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import respx

from jobfinder import paths
from jobfinder.config import load_profile
from jobfinder.discovery.base import LocationQuery, SearchProfile
from jobfinder.discovery.fetch import HttpClient
from jobfinder.discovery.sources.jsearch import JSearchSource

FIX = json.loads(
    (
        Path(__file__).parent.parent
        / "fixtures"
        / "jsearch"
        / "search.json"
    ).read_text()
)


def test_parse() -> None:
    r = JSearchSource(HttpClient(), "k").parse(
        FIX, LocationQuery("canada-any", "CA")
    )[0]
    assert r.source_id == "abc123==" and r.company_name == "Maple Fintech"
    assert (
        r.remote_hint == "remote"
        and r.location_raw == "Toronto, ON, CA"
        and r.description_complete
    )
    assert r.apply_url == "https://jobs.lever.co/maplefintech/1111"
    assert (
        "https://jobs.lever.co/maplefintech/1111" in r.extra["apply_links"]
    )
    assert r.posted_at == datetime(2026, 9, 1, 12, tzinfo=UTC)


def test_parse_reads_the_v2_container_and_the_legacy_list() -> None:
    """2026-09-06: RapidAPI answered 404 \"Endpoint '/search' does not exist\"; JSearch moved to
    /search-v2, where `data` is {jobs: [...], cursor}. Items kept the same field names."""
    q = LocationQuery("canada-any", "CA", None, False)
    src = JSearchSource(HttpClient(), "k")
    v2 = src.parse(FIX, q)
    legacy = src.parse({"status": "OK", "data": FIX["data"]["jobs"]}, q)
    assert v2 and [r.source_id for r in v2] == [r.source_id for r in legacy]
    assert src.parse({"status": "OK", "data": {"jobs": [], "cursor": None}}, q) == []


@respx.mock
def test_fetch_one_call_per_location_rotating_keyword() -> None:
    route = respx.get(
        url__regex=r"https://jsearch\.p\.rapidapi\.com/search-v2.*"
    ).mock(return_value=httpx.Response(200, json=FIX))
    profile_path = paths.repo_root() / "tests" / "fixtures" / "profile.yaml"
    profile = SearchProfile.from_profile(load_profile(profile_path))
    src = JSearchSource(
        HttpClient(), "k", today=date(2026, 9, 3)
    )  # day-of-year 246 % 6 keywords == 0 → "data analyst"
    raws = src.fetch(profile, datetime.now(UTC))
    assert route.call_count == 6  # 5 country queries + 1 remote query
    req = route.calls[0].request
    assert (
        req.headers["x-rapidapi-key"] == "k"
        and req.headers["x-rapidapi-host"] == "jsearch.p.rapidapi.com"
    )
    assert (
        req.url.params["query"] == "data analyst in Canada"
        and req.url.params["date_posted"] == "week"
    )
    remote = route.calls[-1].request.url.params
    # remote_jobs_only is unconfirmed (api-notes.md 2026-09-04);
    # "remote" is folded into the free-text query instead.
    assert (
        remote["query"] == "data analyst in Canada remote"
        and "remote_jobs_only" not in remote
    )
    assert len(raws) == 6


def test_linkedin_only_apply_options_still_yield_a_url() -> None:
    """T11c: no non-LinkedIn link → fall back to job_apply_link, then to the LinkedIn one.
    url/apply_url must never be None (the Posting columns are non-nullable)."""
    base = {
        "job_id": "x1",
        "job_title": "Data Analyst",
        "employer_name": "Acme",
        "job_description": "SQL",
    }
    linkedin = "https://www.linkedin.com/jobs/view/1234567890"
    query = LocationQuery("canada-any", "CA")
    src = JSearchSource(HttpClient(), "k")

    only_linkedin = src.parse(
        {"data": [{**base, "apply_options": [{"apply_link": linkedin}]}]}, query
    )[0]
    assert only_linkedin.apply_url == linkedin and only_linkedin.url == linkedin

    with_job_apply_link = src.parse(
        {"data": [{
            **base,
            "apply_options": [{"apply_link": linkedin}],
            "job_apply_link": "https://acme.test/careers/1",
        }]},
        query,
    )[0]
    assert with_job_apply_link.apply_url == "https://acme.test/careers/1"

    # no link anywhere → dropped, not inserted with a None url
    assert src.parse({"data": [base]}, query) == []
