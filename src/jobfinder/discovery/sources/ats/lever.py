from __future__ import annotations

from typing import Any

from jobfinder.discovery.base import RawPosting
from jobfinder.discovery.sources.ats.base import ATSBoardSource


class LeverSource(ATSBoardSource):
    """Lever postings API adapter.

    api-notes.md 2026-09-04: `?mode=json` on the postings URL is confirmed (github.com/
    lever/postings-api) — unchanged from the draft. `createdAt` (the draft's assumed
    ms-since-epoch creation timestamp) does NOT appear anywhere in Lever's own README, checked
    twice (rendered + raw markdown); `posted_at` is therefore always `None` here. Recency for
    this source comes from repeated polling and dedupe's `first_seen_at` (Task 4), not from a
    per-posting timestamp.
    """

    ats_type = "lever"
    name = "ats_lever"

    def board_url(self, token: str) -> str:
        return f"https://api.lever.co/v0/postings/{token}?mode=json"

    def parse_board(self, payload: Any, token: str) -> list[RawPosting]:
        raws: list[RawPosting] = []
        for job in payload if isinstance(payload, list) else []:
            try:
                cats = job.get("categories") or {}
                lists = "".join(
                    f"<h3>{lst.get('text', '')}</h3><ul>{lst.get('content', '')}</ul>"
                    for lst in job.get("lists", [])
                )
                desc = f"<p>{job.get('descriptionPlain', '')}</p>{lists}"
                wt = (job.get("workplaceType") or "").lower() or None
                raws.append(RawPosting(
                    source=self.name, source_id=str(job["id"]), url=job["hostedUrl"],
                    apply_url=job.get("applyUrl") or job["hostedUrl"], title=job["text"],
                    company_name=token,
                    location_raw=cats.get("location"),
                    remote_hint=wt if wt in ("remote", "hybrid", "onsite") else None,
                    description=desc, description_is_html=True,
                    posted_at=None,
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return raws
