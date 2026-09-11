from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from jobfinder.discovery.base import LocationQuery, RawPosting, SearchProfile
from jobfinder.discovery.fetch import BudgetExceeded, HttpClient

BASE = "https://jooble.org/api/{key}"


class JoobleSource:
    """Jooble job search adapter.

    api-notes.md 2026-09-04: Jooble is the least-documented service in scope — everything
    beyond the URL shape (`POST https://jooble.org/api/{key}`) and auth mechanism is
    third-party-sourced, because Jooble gates real docs behind manual key approval. Treat this
    adapter as higher-risk than the others; validate every field name against a live response
    before production use. The request body below is trimmed to the two fields that are at
    least third-party confirmed (`keywords`, `location`) — the draft's `page`/`datecreatedfrom`
    aren't independently confirmed anywhere reachable, so recency filtering for this source
    falls to dedupe/prefilter's `max_age_days` check downstream instead. Also: the free tier is
    a 500-request lifetime cap per key, not monthly — budget accordingly.
    """

    name = "jooble"

    def __init__(self, client: HttpClient, api_key: str) -> None:
        self.client, self.api_key = client, api_key

    def fetch(self, profile: SearchProfile, since: datetime) -> list[RawPosting]:
        out: list[RawPosting] = []
        for q in profile.queries_for_country("CA", "GB"):
            for keyword in profile.search_keywords:
                body = {
                    "keywords": keyword,
                    "location": q.where or profile.country_name(q.country),
                }
                try:
                    payload = self.client.post_json(BASE.format(key=self.api_key), body)
                except BudgetExceeded:
                    return out
                out.extend(self.parse(payload, q))
        return out

    def parse(self, payload: dict[str, Any], query: LocationQuery) -> list[RawPosting]:
        raws: list[RawPosting] = []
        for item in payload.get("jobs", []):
            try:
                updated = item.get("updated")
                posted = None
                if updated:
                    posted = datetime.fromisoformat(updated[:19]).replace(tzinfo=UTC)
                raws.append(
                    RawPosting(
                        source=self.name,
                        source_id=str(item["id"]),
                        url=item["link"],
                        apply_url=item["link"],
                        title=item["title"],
                        company_name=item.get("company") or "Unknown",
                        location_raw=item.get("location"),
                        description=item.get("snippet", ""),
                        description_is_html=True,
                        description_complete=False,
                        salary_raw=item.get("salary") or None,
                        posted_at=posted,
                        country_hint=query.country,
                        location_key=query.key,
                        extra={"origin": item.get("source")},
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return raws
