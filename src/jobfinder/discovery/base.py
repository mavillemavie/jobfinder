from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from jobfinder.config import Profile

COUNTRY_NAMES = {"CA": "Canada", "GB": "United Kingdom"}


class SourceError(Exception):
    """A whole request to a source failed."""


@dataclass(frozen=True)
class LocationQuery:
    key: str
    country: str | None = None
    where: str | None = None
    remote_only: bool = False


@dataclass
class SearchProfile:
    title_terms: list[str]
    search_keywords: list[str]
    location_queries: list[LocationQuery]
    max_days_old: int

    @classmethod
    def from_profile(cls, profile: Profile) -> SearchProfile:
        keywords: list[str] = []
        explicit = [t.strip().lower() for t in profile.titles.search_keywords if t.strip()]
        if explicit:
            keywords = list(dict.fromkeys(explicit))
        else:
            for cluster in profile.titles.clusters:
                for term in cluster.include[:2]:
                    if term.lower() not in keywords:
                        keywords.append(term.lower())
        queries: list[LocationQuery] = []
        for loc in profile.enabled_locations():
            if loc.cities:
                where = f"{loc.cities[0]}, {loc.region}" if loc.region else loc.cities[0]
                queries.append(LocationQuery(loc.key, loc.country, where, False))
            elif loc.country:
                queries.append(LocationQuery(loc.key, loc.country, None, False))
            else:
                queries.append(LocationQuery(loc.key, None, None, True))
        return cls(profile.title_terms(), keywords, queries, profile.scoring.max_posting_age_days)

    def queries_for_country(self, *codes: str) -> list[LocationQuery]:
        return [q for q in self.location_queries if q.country in codes]

    @property
    def remote_queries(self) -> list[LocationQuery]:
        return [q for q in self.location_queries if q.remote_only]

    @staticmethod
    def country_name(code: str | None) -> str:
        return COUNTRY_NAMES.get(code or "", code or "")


@dataclass
class RawPosting:
    source: str
    source_id: str
    url: str
    title: str
    company_name: str
    apply_url: str | None = None
    location_raw: str | None = None
    remote_hint: str | None = None
    description: str = ""
    description_is_html: bool = False
    description_complete: bool = True
    salary_raw: str | None = None
    posted_at: datetime | None = None
    country_hint: str | None = None
    location_key: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class JobSource(Protocol):
    name: str

    def fetch(self, profile: SearchProfile, since: datetime) -> list[RawPosting]: ...


def filter_by_title(raws: list[RawPosting], profile: SearchProfile) -> list[RawPosting]:
    """Keep postings whose title matches a cluster term (used by broad feeds and ATS boards)."""
    from jobfinder.discovery.normalize import normalize_title
    from jobfinder.discovery.prefilter import title_matches

    return [r for r in raws if title_matches(normalize_title(r.title)[0], profile.title_terms)]
