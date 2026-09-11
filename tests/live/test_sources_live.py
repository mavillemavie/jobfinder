"""Live smoke tests: one real call per source, never run by the default suite.

Run them deliberately with `uv run pytest -m live tests/live/test_sources_live.py`.
Each keyed adapter is skipped when its key is missing from the environment, so the file
is safe to collect anywhere. Every test makes ONE fetch with a single keyword and a
single location — these burn real quota (Jooble's free tier is a 500-request *lifetime*
cap, JSearch's is 200/month), so keep them at one call apiece.

What they check is field-shape truth, not matching: several adapters were written against
prose docs with no reachable JSON example (see docs/research/api-notes.md), so the point
is that a real response still yields RawPostings with a title and a source_id.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jobfinder.db.models import Company, Posting
from jobfinder.discovery.base import LocationQuery, RawPosting, SearchProfile
from jobfinder.discovery.fetch import HttpClient
from jobfinder.discovery.hydrate import hydrate_posting
from jobfinder.discovery.normalize import normalize_title
from jobfinder.discovery.sources.adzuna import AdzunaSource
from jobfinder.discovery.sources.feeds import JobBankRssSource
from jobfinder.discovery.sources.jooble import JoobleSource
from jobfinder.discovery.sources.jsearch import JSearchSource
from jobfinder.discovery.sources.linkedin_guest import LinkedInGuestSource
from jobfinder.discovery.sources.reed import ReedSource
from jobfinder.settings import get_settings

pytestmark = pytest.mark.live

# Read at import time, before any fixture relocates JOBFINDER_HOME.
SETTINGS = get_settings()


def _needs(*keys: str):
    return pytest.mark.skipif(
        not SETTINGS.has(*keys),
        reason=f"missing {', '.join(k.upper() for k in keys)}",
    )


def _search(country: str = "CA", where: str | None = "Montreal, QC") -> SearchProfile:
    """One keyword, one location — the smallest possible real call."""
    return SearchProfile(
        title_terms=["data analyst"],
        search_keywords=["data analyst"],
        location_queries=[LocationQuery("live", country, where, False)],
        max_days_old=14,
    )


def _since() -> datetime:
    return datetime.now(UTC) - timedelta(days=7)


def _assert_usable(raws: list[RawPosting], adapter: str) -> None:
    assert len(raws) > 0, f"{adapter} returned no postings"
    first = raws[0]
    assert first.title.strip(), f"{adapter}: empty title — field name likely wrong"
    assert str(first.source_id).strip(), f"{adapter}: empty source_id"


@_needs("adzuna_app_id", "adzuna_app_key")
def test_adzuna_live() -> None:
    source = AdzunaSource(HttpClient(), SETTINGS.adzuna_app_id, SETTINGS.adzuna_app_key)
    _assert_usable(source.fetch(_search(), _since()), "adzuna")


@_needs("reed_api_key")
def test_reed_live() -> None:
    source = ReedSource(HttpClient(), SETTINGS.reed_api_key)
    _assert_usable(source.fetch(_search("GB", "London"), _since()), "reed")


@_needs("jooble_api_key")
def test_jooble_live() -> None:
    # Jooble's free tier is a 500-call LIFETIME cap — one call, deliberately.
    source = JoobleSource(HttpClient(), SETTINGS.jooble_api_key)
    _assert_usable(source.fetch(_search(), _since()), "jooble")


@_needs("rapidapi_key")
def test_jsearch_live() -> None:
    source = JSearchSource(HttpClient(), SETTINGS.rapidapi_key)
    _assert_usable(source.fetch(_search(), _since()), "jsearch")


def test_jobbank_two_step_flow_live() -> None:
    """No key, but the most fragile adapter: an unofficial session page → embedded feed."""
    _assert_usable(JobBankRssSource(HttpClient()).fetch(_search(), _since()), "jobbank_rss")


def test_linkedin_guest_search_then_detail_hydration_live(db_session) -> None:
    """The guest detail endpoint is what hydration actually fetches for LinkedIn cards."""
    client = HttpClient()
    cards = LinkedInGuestSource(client).fetch(_search(), _since())
    if not cards:
        pytest.skip("linkedin guest search returned no cards (block page or empty result)")
    _assert_usable(cards, "linkedin_guest")

    raw = cards[0]
    posting = Posting(
        company=Company(name=raw.company_name, normalized_name=raw.company_name.lower()),
        title=raw.title,
        normalized_title=normalize_title(raw.title)[0],
        apply_url=raw.apply_url or raw.url,
        dedupe_key="live-linkedin",
        content_hash="",
        description_text="",
        description_complete=False,
    )
    db_session.add(posting)
    db_session.commit()

    assert hydrate_posting(db_session, posting, client) is True
    assert posting.description_complete is True
    assert len(posting.description_text) > 200
