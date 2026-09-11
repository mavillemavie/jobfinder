from __future__ import annotations

import html
from datetime import datetime
from typing import Any

from jobfinder.discovery.base import RawPosting
from jobfinder.discovery.sources.ats.base import ATSBoardSource


class GreenhouseSource(ATSBoardSource):
    ats_type = "greenhouse"
    name = "ats_greenhouse"

    def board_url(self, token: str) -> str:
        return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"

    def parse_board(self, payload: Any, token: str) -> list[RawPosting]:
        raws: list[RawPosting] = []
        for job in payload.get("jobs", []):
            try:
                updated = job.get("updated_at")
                raws.append(RawPosting(
                    source=self.name, source_id=str(job["id"]), url=job["absolute_url"],
                    apply_url=job["absolute_url"],
                    title=job["title"], company_name=token,
                    location_raw=(job.get("location") or {}).get("name"),
                    description=html.unescape(job.get("content", "")), description_is_html=True,
                    posted_at=datetime.fromisoformat(updated) if updated else None,
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return raws
