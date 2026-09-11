from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from jobfinder.discovery.base import RawPosting
from jobfinder.discovery.sources.ats.base import ATSBoardSource


def _ashby_id(job: dict[str, Any]) -> str | None:
    """`id` field presence on the public job-board API is unconfirmed (api-notes.md
    2026-09-04) — fall back to a stable hash of `jobUrl` rather than dropping the item."""
    if job.get("id"):
        return str(job["id"])
    job_url = job.get("jobUrl")
    return hashlib.sha1(job_url.encode()).hexdigest()[:16] if job_url else None


class AshbySource(ATSBoardSource):
    ats_type = "ashby"
    name = "ats_ashby"

    def board_url(self, token: str) -> str:
        return f"https://api.ashbyhq.com/posting-api/job-board/{token}"

    def parse_board(self, payload: Any, token: str) -> list[RawPosting]:
        raws: list[RawPosting] = []
        for job in payload.get("jobs", []):
            try:
                source_id = _ashby_id(job)
                if not source_id:
                    continue
                pub = job.get("publishedAt")
                raws.append(RawPosting(
                    source=self.name, source_id=source_id, url=job["jobUrl"],
                    apply_url=job.get("applyUrl") or job["jobUrl"], title=job["title"],
                    company_name=token,
                    location_raw=job.get("location"),
                    remote_hint="remote" if job.get("isRemote") else None,
                    description=job.get("descriptionHtml") or job.get("descriptionPlain", ""),
                    description_is_html=bool(job.get("descriptionHtml")),
                    posted_at=datetime.fromisoformat(pub.replace("Z", "+00:00")) if pub else None,
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return raws
