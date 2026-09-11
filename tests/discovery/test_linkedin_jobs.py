"""Fantastic Jobs' LinkedIn Job Search API (RapidAPI): one filtered request a day replaces the
guest scraper that 429s. Fixture captured live 2026-09-06 (trimmed)."""
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import respx

from jobfinder import paths
from jobfinder.config import load_profile
from jobfinder.discovery.base import SearchProfile
from jobfinder.discovery.fetch import HttpClient
from jobfinder.discovery.sources.linkedin_jobs import HOST, LinkedInJobsSource

FIX = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "linkedin_jobs" / "active.json").read_text()
)


def _profile() -> SearchProfile:
    prof = load_profile(paths.repo_root() / "tests" / "fixtures" / "profile.yaml")
    prof.titles.search_keywords = ["data analyst", "workforce analyst"]
    return SearchProfile.from_profile(prof)


def test_parse_maps_fields_and_drops_items_without_a_link() -> None:
    raws = LinkedInJobsSource(HttpClient(), "k").parse(FIX)
    assert [r.title for r in raws] == [j["title"] for j in FIX[:3]]  # the linkless 4th is dropped
    uk_remote, leeds, ca_remote = raws
    assert uk_remote.source == "linkedin_jobs" and uk_remote.source_id == str(FIX[0]["linkedin_id"])
    assert uk_remote.company_name == "Jobright.ai" and uk_remote.url.startswith("https://uk.linkedin.com/")
    assert uk_remote.country_hint == "GB" and uk_remote.remote_hint == "remote"
    assert uk_remote.description_complete and not uk_remote.description_is_html
    assert uk_remote.description.startswith("Jobright is your personal AI")
    assert uk_remote.posted_at == datetime(2026, 9, 6, 20, 29, 30, 485000, tzinfo=UTC)
    assert leeds.location_raw == "Leeds, England, United Kingdom" and leeds.remote_hint == "hybrid"
    assert leeds.extra["seniority"] == "Mid-Senior level"
    assert ca_remote.country_hint == "CA" and ca_remote.location_raw == "Canada"
    assert uk_remote.extra["recruitment_agency"] is True


@respx.mock
def test_fetch_is_one_request_with_or_title_and_country_names() -> None:
    route = respx.get(url__regex=rf"https://{HOST}/active-jb.*").mock(
        return_value=httpx.Response(200, json=FIX)
    )
    src = LinkedInJobsSource(
        HttpClient(), "k", page_size=8, exclude_organizations=["Jobright.ai"], agencies="exclude"
    )
    raws = src.fetch(_profile(), datetime.now(UTC) - timedelta(hours=20))
    assert route.call_count == 1 and len(raws) == 3
    req = route.calls[0].request
    assert req.headers["x-rapidapi-key"] == "k" and req.headers["x-rapidapi-host"] == HOST
    p = req.url.params
    assert p["title"] == '"data analyst" OR "workforce analyst"'
    assert p["location"] == 'Canada OR "United Kingdom"'
    assert p["time_frame"] == "24h" and p["limit"] == "8" and p["description_format"] == "text"
    assert p["exclude_organization"] == "Jobright.ai" and p["organization_agency"] == "exclude"


@respx.mock
def test_fetch_widens_the_window_after_a_missed_day() -> None:
    route = respx.get(url__regex=rf"https://{HOST}/active-jb.*").mock(
        return_value=httpx.Response(200, json=[])
    )
    LinkedInJobsSource(HttpClient(), "k").fetch(_profile(), datetime.now(UTC) - timedelta(days=3))
    p = route.calls[0].request.url.params
    assert p["time_frame"] == "7d" and "organization_agency" not in p  # default: include
