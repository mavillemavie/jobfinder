from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from jobfinder.discovery.base import RawPosting
from jobfinder.discovery.sources.ats.base import ATSBoardSource


class WorkableSource(ATSBoardSource):
    """Workable careers-page widget adapter.

    api-notes.md 2026-09-04: sources disagree on whether `description`/`requirements` are
    present with `details=true` — one help-article sample didn't show them, a separate search
    result claimed they are included. Both are read with `.get` defaults below, and
    `description_complete` reflects whether either was actually present: when both are empty,
    the posting is left incomplete so Task 6's hydrate step fetches the full listing page
    instead of assuming this response is the whole description.
    """

    ats_type = "workable"
    name = "ats_workable"

    def board_url(self, token: str) -> str:
        return f"https://www.workable.com/api/accounts/{token}?details=true"

    def parse_board(self, payload: Any, token: str) -> list[RawPosting]:
        raws: list[RawPosting] = []
        for job in payload.get("jobs", []):
            try:
                loc = job.get("location") or {}
                location_raw = loc.get("location_str") or ", ".join(
                    p for p in (loc.get("city"), loc.get("country")) if p
                ) or None
                remote_hint = (
                    "remote"
                    if loc.get("telecommuting")
                    or (loc.get("workplace_type") or "").lower() == "remote"
                    else None
                )
                country_hint = (loc.get("country_code") or "").upper() or None
                created = job.get("created_at")
                posted_at = None
                if created:
                    parsed = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    posted_at = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
                desc_parts = []
                if job.get("description"):
                    desc_parts.append(job["description"])
                if job.get("requirements"):
                    desc_parts.append(f"<h3>Requirements</h3>{job['requirements']}")
                desc = "".join(desc_parts)
                raws.append(RawPosting(
                    source=self.name, source_id=str(job["shortcode"]), url=job["url"],
                    apply_url=job.get("application_url") or job["url"], title=job["title"],
                    company_name=token,
                    location_raw=location_raw,
                    remote_hint=remote_hint,
                    country_hint=country_hint,
                    description=desc, description_is_html=True, description_complete=bool(desc),
                    posted_at=posted_at,
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return raws
