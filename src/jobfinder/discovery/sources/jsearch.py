from __future__ import annotations

from datetime import date, datetime
from typing import Any

from jobfinder.discovery.base import LocationQuery, RawPosting, SearchProfile
from jobfinder.discovery.fetch import BudgetExceeded, HttpClient

# 2026-09-06: "/search" answers 404 "Endpoint '/search' does not exist"; the API moved to
# /search-v2 (same host through RapidAPI), where `data` is {jobs: [...], cursor}.
SEARCH = "https://jsearch.p.rapidapi.com/search-v2"
HOST = "jsearch.p.rapidapi.com"


class JSearchSource:
    """JSearch (RapidAPI) adapter.

    api-notes.md 2026-09-04: the remote-filter parameter name is a documented uncertainty — a
    same-vendor mirror of the docs lists `work_from_home`, not the draft's `remote_jobs_only`,
    and a separate web search named neither. Rather than send an unconfirmed param, remote
    queries append "remote" to the free-text `query` string instead.
    """

    name = "jsearch"

    def __init__(self, client: HttpClient, rapidapi_key: str, today: date | None = None) -> None:
        self.client, self.key, self.today = client, rapidapi_key, today or date.today()

    def _keyword(self, profile: SearchProfile) -> str:
        keywords = profile.search_keywords
        return keywords[self.today.timetuple().tm_yday % len(keywords)]

    def fetch(self, profile: SearchProfile, since: datetime) -> list[RawPosting]:
        out: list[RawPosting] = []
        keyword = self._keyword(profile)
        headers = {"x-rapidapi-key": self.key, "x-rapidapi-host": HOST}
        queries = profile.queries_for_country("CA", "GB") + profile.remote_queries
        for q in queries:
            place = q.where or profile.country_name(q.country) or "Canada"
            query_text = f"{keyword} in {place}"
            if q.remote_only:
                query_text += " remote"
            params: dict[str, Any] = {
                "query": query_text, "page": 1, "num_pages": 1, "date_posted": "week",
            }
            if q.country:
                params["country"] = q.country.lower()
            if q.remote_only:
                params["country"] = "ca"
            try:
                payload = self.client.get_json(SEARCH, params=params, headers=headers)
            except BudgetExceeded:
                return out
            out.extend(self.parse(payload, q))
        return out

    def parse(self, payload: dict[str, Any], query: LocationQuery) -> list[RawPosting]:
        raws: list[RawPosting] = []
        data = payload.get("data") or []
        items = data.get("jobs") or [] if isinstance(data, dict) else data
        for item in items:
            try:
                options = item.get("apply_options", [])
                links = [o.get("apply_link") for o in options if o.get("apply_link")]
                non_linkedin = (
                    link for link in links if "linkedin.com" not in link
                )
                # Prefer a direct (non-LinkedIn) apply link, then JSearch's own
                # job_apply_link, then whatever LinkedIn link we have — url/apply_url are
                # non-nullable downstream, so a posting with no link at all is dropped
                # rather than inserted with a None apply_url.
                apply = (
                    next(non_linkedin, None)
                    or item.get("job_apply_link")
                    or (links[0] if links else None)
                )
                if not apply:
                    continue
                parts = [
                    item.get("job_city"),
                    item.get("job_state"),
                    item.get("job_country"),
                ]
                posted = item.get("job_posted_at_datetime_utc")
                salary = None
                min_sal = item.get("job_min_salary")
                max_sal = item.get("job_max_salary")
                if min_sal or max_sal:
                    salary = f"{int(min_sal or 0)}-{int(max_sal or 0)}"
                raws.append(
                    RawPosting(
                        source=self.name,
                        source_id=str(item["job_id"]),
                        url=item.get("job_apply_link") or apply,
                        apply_url=apply,
                        title=item["job_title"],
                        company_name=item.get("employer_name") or "Unknown",
                        location_raw=", ".join(p for p in parts if p) or None,
                        remote_hint=(
                            "remote" if item.get("job_is_remote") else None
                        ),
                        description=item.get("job_description", ""),
                        description_complete=True,
                        salary_raw=salary,
                        posted_at=(
                            datetime.fromisoformat(
                                posted.replace("Z", "+00:00")
                            )
                            if posted
                            else None
                        ),
                        country_hint=(
                            item.get("job_country")
                            or query.country
                            or None
                        ),
                        location_key=query.key,
                        extra={
                            "apply_links": links,
                            "employer_website": item.get("employer_website"),
                            "publisher": item.get("job_publisher"),
                        },
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return raws
