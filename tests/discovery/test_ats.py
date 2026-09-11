import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from jobfinder import paths
from jobfinder.config import load_profile
from jobfinder.db.models import Company
from jobfinder.discovery.base import SearchProfile
from jobfinder.discovery.fetch import HttpClient, RateLimited
from jobfinder.discovery.sources.ats.ashby import AshbySource
from jobfinder.discovery.sources.ats.greenhouse import GreenhouseSource
from jobfinder.discovery.sources.ats.lever import LeverSource
from jobfinder.discovery.sources.ats.smartrecruiters import SmartRecruitersSource
from jobfinder.discovery.sources.ats.workable import WorkableSource

FIX = Path(__file__).parent.parent / "fixtures" / "ats"


def _load(name: str):
    return json.loads((FIX / f"{name}.json").read_text())


def _profile() -> SearchProfile:
    fixture = paths.repo_root() / "tests" / "fixtures" / "profile.yaml"
    return SearchProfile.from_profile(load_profile(fixture))


def _companies(ats: str):
    return [Company(name="Acme", normalized_name="acme", ats_type=ats, ats_board_token="acme")]


def test_greenhouse_parse_unescapes_content() -> None:
    r = GreenhouseSource(HttpClient(), _companies).parse_board(_load("greenhouse"), "acme")[0]
    assert (r.source_id, r.title, r.location_raw) == (
        "4001", "Business Intelligence Analyst", "Vancouver, BC",
    )
    assert r.description_is_html and "<p>Own our Looker & SQL" in r.description
    assert r.posted_at == datetime(2026, 9, 1, 14, 0, tzinfo=UTC)


def test_lever_parse_joins_lists() -> None:
    r = LeverSource(HttpClient(), _companies).parse_board(_load("lever"), "acme")[0]
    assert (r.source_id, r.title, r.remote_hint, r.location_raw) == (
        "a1b2", "Analytics Engineer", "remote", "Canada",
    )
    assert "Requirements" in r.description and "dbt" in r.description
    # createdAt is not a documented Lever field (api-notes.md 2026-09-04, checked twice against
    # Lever's own README) — recency for this source comes from dedupe's first_seen_at instead.
    assert r.posted_at is None


def test_ashby_and_workable_parse() -> None:
    a_raws = AshbySource(HttpClient(), _companies).parse_board(_load("ashby"), "acme")
    a = a_raws[0]
    assert (a.source_id, a.title, a.location_raw, a.remote_hint) == (
        "c3d4", "Data Analyst", "Edmonton, AB", None,
    )
    # Second ashby.json job has no "id" — api-notes.md flags id-field presence as unconfirmed;
    # parse_board falls back to a stable hash of jobUrl.
    a2 = a_raws[1]
    assert a2.source_id == "5c3f256727e62c8d" and a2.title == "Reporting Analyst"

    w_raws = WorkableSource(HttpClient(), _companies).parse_board(_load("workable"), "acme")
    w = w_raws[0]
    assert (w.source_id, w.title, w.location_raw, w.remote_hint) == (
        "E5F6", "Reporting Analyst", "Montreal, Quebec, Canada", "remote",
    )
    # location_raw/remote_hint/country_hint/posted_at all come from the nested `location` object
    # and `created_at` per api-notes.md 2026-09-04 (Workable widget API), not flat fields.
    assert w.country_hint == "CA"
    assert w.posted_at == datetime(2026, 9, 1, tzinfo=UTC)
    assert "Tableau" in w.description and "SQL" in w.description and w.description_complete is True
    # Second workable.json job has neither description nor requirements — api-notes.md flags
    # their presence with details=true as unresolved; description_complete stays False so
    # Task 6's hydrate step fetches the full listing page instead of assuming this is complete.
    w2 = w_raws[1]
    assert w2.description == "" and w2.description_complete is False


@respx.mock
def test_smartrecruiters_fetch_lists_then_details_matching_only() -> None:
    list_url = r"https://api\.smartrecruiters\.com/v1/companies/acme/postings(\?.*)?$"
    respx.get(url__regex=list_url).mock(
        return_value=httpx.Response(200, json=_load("smartrecruiters_list"))
    )
    detail = respx.get("https://api.smartrecruiters.com/v1/companies/acme/postings/7788").mock(
        return_value=httpx.Response(200, json=_load("smartrecruiters_detail"))
    )
    raws = SmartRecruitersSource(HttpClient(), _companies).fetch(_profile(), datetime.now(UTC))
    # 2 title-matching jobs in the fixture, but only the one carrying `ref` costs a detail call.
    assert detail.call_count == 1 and len(raws) == 2
    r = next(x for x in raws if x.source_id == "7788")
    assert (r.source_id, r.country_hint, r.location_raw) == ("7788", "GB", "London, England, gb")
    # url/apply_url come from the detail response's documented `postingUrl`/`applyUrl` fields
    # (api-notes.md 2026-09-04), not the guessed `{token}/{id}` pattern.
    assert r.url == "https://jobs.smartrecruiters.com/acme/7788-data-analyst"
    assert r.apply_url.startswith("https://jobs.smartrecruiters.com/acme/")
    assert "Qualifications" in r.description
    # jobAd has fixed named keys directly (no sections wrapper) — api-notes.md 2026-09-04.
    assert "SQL and Power BI" in r.description and r.description_complete is True


@respx.mock
def test_smartrecruiters_missing_ref_keeps_listing_posting_with_human_url() -> None:
    list_url = r"https://api\.smartrecruiters\.com/v1/companies/acme/postings(\?.*)?$"
    respx.get(url__regex=list_url).mock(
        return_value=httpx.Response(200, json=_load("smartrecruiters_list"))
    )
    respx.get("https://api.smartrecruiters.com/v1/companies/acme/postings/7788").mock(
        return_value=httpx.Response(200, json=_load("smartrecruiters_detail"))
    )
    raws = SmartRecruitersSource(HttpClient(), _companies).fetch(_profile(), datetime.now(UTC))
    no_ref = next(x for x in raws if x.source_id == "7799")
    # No `ref` → no detail call, no None deref; url/apply_url fall back to the human-facing
    # guessed public-site URL (T14c) and the body is left for hydration.
    assert no_ref.url == "https://jobs.smartrecruiters.com/acme/7799"
    assert no_ref.apply_url == "https://jobs.smartrecruiters.com/acme/7799"
    assert no_ref.extra["ref"] is None
    assert no_ref.description == "" and no_ref.description_complete is False


@respx.mock
def test_smartrecruiters_detail_429_propagates() -> None:
    list_url = r"https://api\.smartrecruiters\.com/v1/companies/acme/postings(\?.*)?$"
    respx.get(url__regex=list_url).mock(
        return_value=httpx.Response(200, json=_load("smartrecruiters_list"))
    )
    respx.get("https://api.smartrecruiters.com/v1/companies/acme/postings/7788").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "60"})
    )
    with pytest.raises(RateLimited):
        SmartRecruitersSource(HttpClient(retries=0), _companies).fetch(
            _profile(), datetime.now(UTC)
        )


@respx.mock
def test_board_fetch_iterates_companies_and_filters_titles() -> None:
    respx.get(url__regex=r"https://boards-api\.greenhouse\.io/v1/boards/acme/jobs.*").mock(
        return_value=httpx.Response(200, json=_load("greenhouse"))
    )
    raws = GreenhouseSource(HttpClient(), _companies).fetch(_profile(), datetime.now(UTC))
    assert len(raws) == 1 and raws[0].company_name == "Acme"
    assert GreenhouseSource(HttpClient(), lambda ats: []).fetch(_profile(), datetime.now(UTC)) == []
