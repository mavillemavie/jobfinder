from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from jobfinder.discovery.base import LocationQuery, RawPosting, SearchProfile, SourceError
from jobfinder.discovery.fetch import BudgetExceeded, HttpClient, RateLimited

log = logging.getLogger(__name__)

BASE = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"


class AdzunaSource:
    """Adzuna job search adapter.

    api-notes.md 2026-09-04: `what` (not the draft's `title_only`) is the confirmed primary
    keyword param. `distance` and `sort_by=date` are dropped — neither is confirmed documented
    (Adzuna's docs only confirm `sort_by=salary`), so we don't assume they work. `max_days_old`
    is kept even though it's absent from the primary docs page, because api-notes.md confirms
    it present and working in a third-party MCP server's actual request code (2026-09-04).
    """

    name = "adzuna"

    def __init__(self, client: HttpClient, app_id: str, app_key: str) -> None:
        self.client, self.app_id, self.app_key = client, app_id, app_key

    def fetch(self, profile: SearchProfile, since: datetime) -> list[RawPosting]:
        out: list[RawPosting] = []
        failed: list[SourceError] = []
        attempted = 0
        for q in profile.queries_for_country("CA", "GB"):
            for keyword in profile.search_keywords:
                params: dict[str, Any] = {
                    "app_id": self.app_id, "app_key": self.app_key,
                    "what": keyword, "results_per_page": 50,
                    "max_days_old": profile.max_days_old, "content-type": "application/json",
                }
                if q.where:
                    params["where"] = q.where
                url = BASE.format(country=(q.country or "ca").lower(), page=1)
                attempted += 1
                try:
                    payload = self.client.get_json(url, params=params)
                except BudgetExceeded:
                    return out
                except RateLimited:
                    raise
                except SourceError as exc:
                    # Adzuna answers 503 per request, not per key (2026-09-06: one keyword
                    # failed three times while the others were fine). Losing one query is
                    # a gap; losing the whole source, and its cooldown, cost the day's run.
                    log.warning("adzuna query %r/%s skipped: %s", keyword, q.key, exc)
                    failed.append(exc)
                    continue
                out.extend(self.parse(payload, q))
        if failed and len(failed) == attempted:
            raise failed[-1]
        return out

    def parse(self, payload: dict[str, Any], query: LocationQuery) -> list[RawPosting]:
        raws: list[RawPosting] = []
        for item in payload.get("results", []):
            try:
                salary = None
                if item.get("salary_min") or item.get("salary_max"):
                    salary_min = int(item.get("salary_min") or 0)
                    salary_max = int(item.get("salary_max") or 0)
                    salary = f"{salary_min}-{salary_max}"
                created = item.get("created")
                posted_at = None
                if created:
                    posted_at = datetime.fromisoformat(created.replace("Z", "+00:00"))
                raws.append(RawPosting(
                    source=self.name, source_id=str(item["id"]),
                    url=item["redirect_url"], apply_url=item["redirect_url"],
                    title=item["title"],
                    company_name=(item.get("company") or {}).get("display_name") or "Unknown",
                    location_raw=(item.get("location") or {}).get("display_name"),
                    description=item.get("description", ""),
                    description_complete=False, salary_raw=salary, posted_at=posted_at,
                    country_hint=query.country, location_key=query.key,
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return raws
