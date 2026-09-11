import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from jobfinder import paths
from jobfinder.config import load_profile
from jobfinder.discovery.base import LocationQuery, SearchProfile
from jobfinder.discovery.fetch import HttpClient, RateLimited
from jobfinder.discovery.sources.feeds import (
    JobBankRssSource,
    RemotiveSource,
    WeWorkRemotelySource,
    _field,
)

FIX = Path(__file__).parent.parent / "fixtures" / "feeds"


def _profile() -> SearchProfile:
    fixture = paths.repo_root() / "tests" / "fixtures" / "profile.yaml"
    return SearchProfile.from_profile(load_profile(fixture))


def test_jobbank_parse() -> None:
    query = LocationQuery("montreal", "CA", "Montreal, QC")
    raws = JobBankRssSource(HttpClient()).parse((FIX / "jobbank.xml").read_text(), query)
    r = raws[0]
    assert (r.source_id, r.title, r.company_name) == ("43210001", "Data analyst", "Ville Data Inc.")
    assert r.location_raw == "Montréal, Québec" and r.salary_raw == "$32.00 hourly"
    assert (
        r.posted_at == datetime(2026, 9, 2, 10, 15, tzinfo=UTC)
        and r.description_complete is False
    )


@respx.mock
def test_jobbank_fetch_two_step_session_then_atom_feed() -> None:
    session_route = respx.get(url__regex=r"https://www\.jobbank\.gc\.ca/jobsearch/jobsearch\?.*").mock(
        return_value=httpx.Response(
            200, text=(FIX / "jobbank_session.html").read_text(),
            headers={"Set-Cookie": "JSESSIONID=ABCDEF0123456789.jobsearch76; Path=/"},
        )
    )
    feed_route = respx.get(url__regex=r"https://www\.jobbank\.gc\.ca/jobsearch/feed/jobSearchRSSfeed.*").mock(
        return_value=httpx.Response(200, text=(FIX / "jobbank.xml").read_text())
    )
    raws = JobBankRssSource(HttpClient()).fetch(_profile(), datetime.now(UTC))
    # 4 CA queries × 4 keywords
    assert session_route.call_count == 24 and feed_route.call_count == 24  # 6 keywords × 4 CA
    assert session_route.calls[0].request.url.params["searchstring"] == "data analyst"
    assert len(raws) == 24


@respx.mock
def test_jobbank_session_429_propagates_rate_limited() -> None:
    """A 429 is adapter-wide: it must escape fetch() so run_scan sets the cooldown,
    instead of being logged per keyword and retried 15 more times."""
    respx.get(url__regex=r"https://www\.jobbank\.gc\.ca/jobsearch/jobsearch\?.*").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "120"})
    )
    with pytest.raises(RateLimited) as exc:
        JobBankRssSource(HttpClient(retries=0)).fetch(_profile(), datetime.now(UTC))
    assert exc.value.retry_after == 120


@respx.mock
def test_jobbank_fetch_returns_empty_and_warns_when_session_step_yields_no_feed_link(
    caplog,
) -> None:
    respx.get(url__regex=r"https://www\.jobbank\.gc\.ca/jobsearch/jobsearch\?.*").mock(
        return_value=httpx.Response(200, text="<html><body>no results</body></html>")
    )
    with caplog.at_level("WARNING"):
        raws = JobBankRssSource(HttpClient()).fetch(_profile(), datetime.now(UTC))
    assert raws == [] and "jobbank_rss" in caplog.text


def test_jobbank_non_atom_response_warns_and_returns_empty(caplog) -> None:
    query = LocationQuery("montreal", "CA", "Montreal, QC")
    html_response = "<html><body>Access denied</body></html>"
    with caplog.at_level("WARNING"):
        raws = JobBankRssSource(HttpClient()).parse(html_response, query)
    assert raws == []
    assert "not an Atom feed" in caplog.text


def test_remotive_parse_and_fetch() -> None:
    payload = json.loads((FIX / "remotive.json").read_text())
    r = RemotiveSource(HttpClient()).parse(payload)[0]
    assert (r.source_id, r.company_name, r.remote_hint, r.location_raw) == (
        "1900001", "Orbit Health", "remote", "Canada",
    )
    assert (
        r.description_is_html
        and r.description_complete
        and r.location_key == "remote-from-canada"
    )
    with respx.mock:
        route = respx.get(url__regex=r"https://remotive\.com/api/remote-jobs.*").mock(
            return_value=httpx.Response(200, json=payload)
        )
        raws = RemotiveSource(HttpClient()).fetch(_profile(), datetime.now(UTC))
        assert route.call_count == 6 and len(raws) == 6  # one call per keyword


@respx.mock
def test_wwr_fetch_filters_by_title_cluster() -> None:
    respx.get("https://weworkremotely.com/remote-jobs.rss").mock(
        return_value=httpx.Response(200, text=(FIX / "wwr.xml").read_text())
    )
    raws = WeWorkRemotelySource(HttpClient()).fetch(_profile(), datetime.now(UTC))
    assert len(raws) == 1
    r = raws[0]
    assert (r.title, r.company_name, r.location_raw, r.remote_hint) == (
        "Analytics Engineer", "Lumen Labs", "Anywhere in the World", "remote",
    )
    assert r.source_id == "lumen-labs-analytics-engineer" and r.description_complete


def test_field_unescapes_entities() -> None:
    summary = '<strong>Employer:</strong> Smith &amp; Co.<br/>'
    assert _field(summary, "Employer", "Employeur") == "Smith & Co."
