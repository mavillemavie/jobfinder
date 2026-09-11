import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import respx

from jobfinder import paths
from jobfinder.config import load_profile
from jobfinder.discovery.base import LocationQuery, SearchProfile
from jobfinder.discovery.fetch import HttpClient
from jobfinder.discovery.sources.reed import ReedSource

FIX = json.loads((Path(__file__).parent.parent / "fixtures" / "reed" / "search.json").read_text())


def test_parse() -> None:
    raws = ReedSource(HttpClient(), "k").parse(FIX, LocationQuery("uk", "GB"))
    r = raws[0]
    assert (
        r.source_id,
        r.title,
        r.company_name,
        r.location_raw,
    ) == ("90001", "Data Analyst", "Northwind Analytics Ltd", "Leeds")
    assert (
        r.posted_at == datetime(2026, 9, 2, tzinfo=UTC) and r.country_hint == "GB"
    )
    assert (
        r.salary_raw == "35000-42000 GBP" and r.description_complete is False
    )

    # Second fixture item has no jobUrl/currency — api-notes.md flags both as unconfirmed
    # fields; the adapter falls back rather than dropping the item.
    r2 = raws[1]
    assert r2.url == "https://www.reed.co.uk/jobs/90002" and r2.salary_raw == "30000-36000 GBP"
    assert r2.posted_at == datetime(2026, 9, 1, tzinfo=UTC)


@respx.mock
def test_fetch_uses_basic_auth_and_gb_queries_only() -> None:
    route = respx.get(url__regex=r"https://www\.reed\.co\.uk/api/1\.0/search.*").mock(
        return_value=httpx.Response(200, json=FIX)
    )
    profile_path = paths.repo_root() / "tests" / "fixtures" / "profile.yaml"
    profile = SearchProfile.from_profile(load_profile(profile_path))
    raws = ReedSource(HttpClient(), "secret").fetch(profile, datetime.now(UTC))
    assert route.call_count == 6  # 1 GB query × 6 keywords
    req = route.calls[0].request
    assert req.headers["Authorization"].startswith("Basic ")
    assert (
        req.url.params["keywords"] == "data analyst"
        and req.url.params["resultsToTake"] == "100"
    )
    assert len(raws) == 12  # 4 calls × 2 fixture items
