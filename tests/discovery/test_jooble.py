import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import respx

from jobfinder import paths
from jobfinder.config import load_profile
from jobfinder.discovery.base import LocationQuery, SearchProfile
from jobfinder.discovery.fetch import HttpClient
from jobfinder.discovery.sources.jooble import JoobleSource

FIX = json.loads((Path(__file__).parent.parent / "fixtures" / "jooble" / "search.json").read_text())


def test_parse() -> None:
    query = LocationQuery("edmonton", "CA", "Edmonton, AB")
    r = JoobleSource(HttpClient(), "k").parse(FIX, query)[0]
    assert (r.source_id, r.title, r.company_name, r.location_raw) == (
        "77001", "Reporting Analyst", "Prairie Health", "Edmonton, AB"
    )
    assert r.description_is_html and r.description_complete is False
    assert r.posted_at == datetime(2026, 9, 2, tzinfo=UTC)


@respx.mock
def test_fetch_posts_json_per_query() -> None:
    route = respx.post("https://jooble.org/api/k").mock(
        return_value=httpx.Response(200, json=FIX)
    )
    profile_path = paths.repo_root() / "tests" / "fixtures" / "profile.yaml"
    profile = SearchProfile.from_profile(load_profile(profile_path))
    since = datetime(2026, 8, 20, tzinfo=UTC)
    raws = JoobleSource(HttpClient(), "k").fetch(profile, since)
    assert route.call_count == 30  # 6 keywords × 5 country queries
    body = json.loads(route.calls[0].request.content)
    assert body["keywords"] == "data analyst" and body["location"] == "Canada"
    # page/datecreatedfrom dropped: not independently confirmed anywhere reachable
    # (api-notes.md 2026-09-04) — only keywords/location are third-party confirmed.
    assert set(body.keys()) == {"keywords", "location"} and len(raws) == 30
