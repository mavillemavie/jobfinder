from datetime import UTC, datetime
from pathlib import Path

import httpx
import respx

from jobfinder import paths
from jobfinder.config import load_profile
from jobfinder.discovery.base import LocationQuery, SearchProfile
from jobfinder.discovery.fetch import HttpClient
from jobfinder.discovery.sources.linkedin_guest import LinkedInGuestSource

HTML = (Path(__file__).parent.parent / "fixtures" / "linkedin" / "search.html").read_text()


def test_parse_cards() -> None:
    query = LocationQuery("montreal", "CA", "Montreal, QC")
    raws = LinkedInGuestSource(HttpClient()).parse(HTML, query)
    assert len(raws) == 2
    r = raws[0]
    assert (r.source_id, r.title, r.company_name) == (
        "4123456789",
        "Data Analyst",
        "Acme Logistics",
    )
    expected_url = "https://ca.linkedin.com/jobs/view/data-analyst-at-acme-4123456789"
    assert r.url == expected_url
    expected_date = datetime(2026, 9, 1, tzinfo=UTC)
    assert r.location_raw == "Montreal, Quebec, Canada" and r.posted_at == expected_date
    assert r.description_complete is False
    assert raws[1].remote_hint == "hybrid"


@respx.mock
def test_fetch_two_keywords_per_country_query() -> None:
    pattern = r"https://www\.linkedin\.com/jobs-guest/jobs/api/seeMoreJobPostings/search.*"
    route = respx.get(url__regex=pattern).mock(
        return_value=httpx.Response(200, text=HTML)
    )
    profile_path = paths.repo_root() / "tests" / "fixtures" / "profile.yaml"
    profile = SearchProfile.from_profile(load_profile(profile_path))
    raws = LinkedInGuestSource(HttpClient()).fetch(profile, datetime.now(UTC))
    assert route.call_count == 10  # 5 country queries × 2 keywords
    p = route.calls[0].request.url.params
    assert (
        p["keywords"] == "data analyst"
        and p["location"] == "Canada"
        and p["f_TPR"] == "r604800"
    )
    assert len(raws) == 20


def test_parse_warns_when_no_cards(caplog) -> None:
    caplog.set_level("WARNING")
    query = LocationQuery("montreal", "CA", "Montreal, QC")
    result = LinkedInGuestSource(HttpClient()).parse(
        "<html><body><div class='authwall'>Sign in</div></body></html>", query
    )
    assert result == []
    assert any("possible block" in record.message for record in caplog.records)
