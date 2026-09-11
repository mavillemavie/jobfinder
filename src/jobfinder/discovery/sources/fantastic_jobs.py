"""Shared client for Fantastic Jobs' RapidAPI feeds (LinkedIn Job Search API, Active Jobs DB).

Both speak the same dialect: one GET with `time_frame` (required), Google-style OR syntax in
`title` / `location`, `description_format=text`, `limit`/`offset`, `exclude_organization`,
`organization_agency`; both answer a JSON list of jobs with the same field names; both bill a
request credit plus one job credit per job returned (free Basic: 25 requests, 250 jobs a
month), so a subclass asks for a small page once a day and never paginates. Verified live
2026-09-06 (docs/research/api-notes.md).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from jobfinder.discovery.base import RawPosting, SearchProfile
from jobfinder.discovery.fetch import BudgetExceeded, HttpClient

log = logging.getLogger(__name__)
DEFAULT_PAGE_SIZE = 8  # ≈ 250 free job credits / 30 days
_COUNTRY_CODES = {"canada": "CA", "united kingdom": "GB", "united states": "US"}


class FantasticJobsSource:
    name = "fantastic_jobs"
    host = ""
    path = ""

    def __init__(
        self,
        client: HttpClient,
        rapidapi_key: str,
        page_size: int | None = None,
        exclude_organizations: list[str] | None = None,
        agencies: str = "include",
    ) -> None:
        self.client, self.key = client, rapidapi_key
        self.page_size = page_size or DEFAULT_PAGE_SIZE
        self.exclude_organizations = list(exclude_organizations or [])
        self.agencies = agencies

    @property
    def url(self) -> str:
        return f"https://{self.host}{self.path}"

    @staticmethod
    def _quote(term: str) -> str:
        return f'"{term}"' if " " in term else term

    def _params(self, profile: SearchProfile, since: datetime) -> dict[str, Any]:
        # Plain `title`/`location` take Google-style OR syntax; `*_advanced` has its own
        # boolean grammar and rejected a quoted OR list live, so it is not used.
        title = " OR ".join(self._quote(k) for k in profile.search_keywords)
        countries: list[str] = []
        for q in profile.location_queries:
            name = profile.country_name(q.country) if q.country else None
            if name and name not in countries:
                countries.append(name)
        location = " OR ".join(self._quote(c) for c in countries) or "Canada"
        age = datetime.now(UTC) - (since if since.tzinfo else since.replace(tzinfo=UTC))
        time_frame = "24h" if age <= timedelta(hours=26) else "7d"
        params: dict[str, Any] = {
            "title": title, "location": location, "time_frame": time_frame,
            "limit": self.page_size, "offset": 0, "description_format": "text",
        }
        if self.exclude_organizations:
            # Verified live for one name (2026-09-06); the API documents comma-separated lists
            # for its other organization filters, so several names are joined the same way.
            params["exclude_organization"] = ",".join(self.exclude_organizations)
        if self.agencies in ("exclude", "only"):
            params["organization_agency"] = self.agencies
        return params

    def fetch(self, profile: SearchProfile, since: datetime) -> list[RawPosting]:
        headers = {"x-rapidapi-key": self.key, "x-rapidapi-host": self.host}
        params = self._params(profile, since)
        try:
            payload = self.client.get_json(self.url, params=params, headers=headers)
        except BudgetExceeded:
            return []
        return self.parse(payload)

    def parse(self, payload: Any) -> list[RawPosting]:
        items = payload if isinstance(payload, list) else (payload or {}).get("data") or []
        raws: list[RawPosting] = []
        for item in items:
            try:
                url = item.get("url")
                if not url:
                    continue  # url/apply_url are non-nullable downstream
                derived = item.get("locations_derived") or []
                countries = item.get("countries_derived") or []
                country = _COUNTRY_CODES.get(str(countries[0]).lower()) if countries else None
                if country is None and derived:
                    # Some ATS-sourced items omit countries_derived; the location string
                    # ends with the country name ("Slough, England, United Kingdom").
                    country = _COUNTRY_CODES.get(str(derived[0]).rsplit(",", 1)[-1].strip().lower())
                arrangement = str(item.get("ai_work_arrangement") or "").lower()
                remote = (
                    "remote" if arrangement.startswith("remote")
                    else "hybrid" if "hybrid" in arrangement else None
                )
                posted = item.get("date_posted")
                posted_at = None
                if posted:
                    posted_at = datetime.fromisoformat(str(posted).replace("Z", "+00:00"))
                    if posted_at.tzinfo is None:
                        posted_at = posted_at.replace(tzinfo=UTC)
                salary = item.get("salary")
                raws.append(RawPosting(
                    source=self.name,
                    source_id=str(item.get("linkedin_id") or item["id"]),
                    url=url,
                    apply_url=url,
                    title=item["title"],
                    company_name=item.get("organization") or "Unknown",
                    location_raw=(derived[0] if derived else None),
                    remote_hint=remote,
                    description=item.get("description_text") or "",
                    description_is_html=False,
                    description_complete=bool(item.get("description_text")),
                    salary_raw=str(salary) if salary else None,
                    posted_at=posted_at,
                    country_hint=country,
                    extra={
                        "seniority": item.get("seniority"),
                        "recruitment_agency": item.get("org_linkedin_recruitment_agency_derived"),
                        "employment_type": item.get("employment_type"),
                        "hiring_manager_name": item.get("ai_hiring_manager_name"),
                        # The ATS an employer-site posting came from (greenhouse, ashby, …).
                        "ats": item.get("source") if item.get("source_type") == "ats" else None,
                    },
                ))
            except Exception:  # noqa: BLE001 — one malformed item never drops the page
                log.exception("%s: could not parse item %s", self.name, str(item.get("id"))[:40])
        return raws
