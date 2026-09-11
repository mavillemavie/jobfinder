"""Fantastic Jobs' Active Jobs DB (RapidAPI): employer career-site postings across 54 ATS
platforms, same conventions as the LinkedIn feed. Fixture captured live 2026-09-06 (trimmed)."""
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import respx

from jobfinder import paths
from jobfinder.config import load_profile
from jobfinder.discovery.base import SearchProfile
from jobfinder.discovery.fetch import HttpClient
from jobfinder.discovery.sources.active_jobs_db import ActiveJobsDbSource

FIX = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "active_jobs_db" / "active.json").read_text()
)


def _profile() -> SearchProfile:
    prof = load_profile(paths.repo_root() / "tests" / "fixtures" / "profile.yaml")
    prof.titles.search_keywords = ["data analyst", "analytics engineer"]
    return SearchProfile.from_profile(prof)


def test_parse_maps_employer_site_postings() -> None:
    raws = ActiveJobsDbSource(HttpClient(), "k").parse(FIX)
    assert len(raws) == 2
    konrad, axle = raws
    assert konrad.source == "active_jobs_db" and konrad.source_id == str(FIX[0]["id"])
    assert konrad.company_name == "Konrad" and konrad.url.startswith("https://www.konrad.com/")
    assert konrad.country_hint == "CA" and konrad.location_raw == "Toronto, Ontario, Canada"
    assert konrad.description_complete and konrad.extra["ats"] == "greenhouse"
    assert konrad.posted_at.tzinfo is not None
    assert axle.country_hint == "GB" and axle.extra["ats"] == "ashby"
    assert axle.url.startswith("https://jobs.ashbyhq.com/")


@respx.mock
def test_fetch_is_one_request_to_active_ats() -> None:
    route = respx.get(url__regex=r"https://active-jobs-db\.p\.rapidapi\.com/active-ats.*").mock(
        return_value=httpx.Response(200, json=FIX)
    )
    src = ActiveJobsDbSource(HttpClient(), "k", page_size=8, agencies="exclude")
    raws = src.fetch(_profile(), datetime.now(UTC) - timedelta(hours=20))
    assert route.call_count == 1 and len(raws) == 2
    req = route.calls[0].request
    assert req.headers["x-rapidapi-host"] == "active-jobs-db.p.rapidapi.com"
    p = req.url.params
    assert p["title"] == '"data analyst" OR "analytics engineer"'
    assert p["location"] == 'Canada OR "United Kingdom"' and p["time_frame"] == "24h"
    assert p["limit"] == "8" and p["description_format"] == "text"
    assert p["organization_agency"] == "exclude"


def test_country_falls_back_to_the_location_string() -> None:
    """Live 2026-09-06: an Eightfold posting had no countries_derived but
    locations_derived 'Slough, England, United Kingdom'."""
    item = {**FIX[1], "countries_derived": None,
            "locations_derived": ["Slough, England, United Kingdom"]}
    raws = ActiveJobsDbSource(HttpClient(), "k").parse([item])
    assert raws[0].country_hint == "GB" and raws[0].location_raw.startswith("Slough")
