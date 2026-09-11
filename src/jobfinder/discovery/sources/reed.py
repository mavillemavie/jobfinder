from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from jobfinder.discovery.base import LocationQuery, RawPosting, SearchProfile
from jobfinder.discovery.fetch import BudgetExceeded, HttpClient

SEARCH = "https://www.reed.co.uk/api/1.0/search"


def _parse_reed_date(value: str | None) -> datetime | None:
    """Reed's date format is not documented anywhere reachable (api-notes.md 2026-09-04 — no
    JSON example is published on any fetched page). Try the commonly-assumed DD/MM/YYYY shape
    first, then fall back to ISO 8601, rather than silently dropping the item on a format we
    didn't anticipate."""
    if not value:
        return None
    try:
        d, m, y = value.split("/")
        return datetime(int(y), int(m), int(d), tzinfo=UTC)
    except (ValueError, TypeError):
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class ReedSource:
    """Reed (UK) job search adapter.

    api-notes.md 2026-09-04: Reed's docs describe response fields only in prose — no JSON
    example exists on any reachable page, so exact camelCase key names are not independently
    confirmed. `currency` and `jobUrl` specifically are flagged as unconfirmed-present; both
    are read defensively below with a documented fallback rather than dropped items. Get a real
    key and make one live test call before trusting this adapter's field names in production.
    """

    name = "reed"

    def __init__(self, client: HttpClient, api_key: str) -> None:
        self.client, self.api_key = client, api_key

    def fetch(self, profile: SearchProfile, since: datetime) -> list[RawPosting]:
        out: list[RawPosting] = []
        for q in profile.queries_for_country("GB"):
            for keyword in profile.search_keywords:
                params: dict[str, Any] = {
                    "keywords": keyword,
                    "resultsToTake": 100,
                    "resultsToSkip": 0,
                }
                if q.where:
                    params["locationName"] = q.where
                    params["distanceFromLocation"] = 30
                try:
                    payload = self.client.get_json(
                        SEARCH, params=params, auth=(self.api_key, "")
                    )
                except BudgetExceeded:
                    return out
                out.extend(self.parse(payload, q))
        return out

    def parse(self, payload: dict[str, Any], query: LocationQuery) -> list[RawPosting]:
        raws: list[RawPosting] = []
        for item in payload.get("results", []):
            try:
                posted = _parse_reed_date(item.get("date"))
                salary = None
                if item.get("minimumSalary") or item.get("maximumSalary"):
                    # `currency` field existence is unconfirmed (api-notes.md); default to GBP.
                    min_sal = int(item.get("minimumSalary") or 0)
                    max_sal = int(item.get("maximumSalary") or 0)
                    currency = item.get("currency") or "GBP"
                    salary = f"{min_sal}-{max_sal} {currency}"
                # `jobUrl` field existence is unconfirmed (api-notes.md); fall back to a
                # constructed Reed job URL from `jobId` rather than dropping the item.
                job_url = item.get("jobUrl") or (
                    f"https://www.reed.co.uk/jobs/{item['jobId']}"
                )
                raws.append(
                    RawPosting(
                        source=self.name,
                        source_id=str(item["jobId"]),
                        url=job_url,
                        apply_url=job_url,
                        title=item["jobTitle"],
                        company_name=item.get("employerName") or "Unknown",
                        location_raw=item.get("locationName"),
                        description=item.get("jobDescription", ""),
                        description_complete=False,
                        salary_raw=salary,
                        posted_at=posted,
                        country_hint="GB",
                        location_key=query.key,
                        extra={"employer_id": item.get("employerId")},
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return raws
