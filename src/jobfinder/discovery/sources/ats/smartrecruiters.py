from __future__ import annotations

from datetime import datetime
from typing import Any

from jobfinder.discovery.base import RawPosting, SearchProfile, SourceError
from jobfinder.discovery.fetch import BudgetExceeded, RateLimited
from jobfinder.discovery.normalize import normalize_title
from jobfinder.discovery.prefilter import title_matches
from jobfinder.discovery.sources.ats.base import ATSBoardSource


class SmartRecruitersSource(ATSBoardSource):
    """SmartRecruiters posting API adapter.

    api-notes.md 2026-09-04: the detail endpoint's `jobAd` is NOT a generic `{title, text}`
    sections array (or a `sections` dict of such objects) as an earlier draft assumed — it's an
    object with fixed, named keys (`companyDescription`, `jobDescription`, `qualifications`,
    `additionalInformation`, `videos`; `videos` holds a `urls` list instead of text).
    `fetch_board` below reads those keys directly.
    """

    ats_type = "smartrecruiters"
    name = "ats_smartrecruiters"

    def board_url(self, token: str) -> str:
        return f"https://api.smartrecruiters.com/v1/companies/{token}/postings"

    def fetch_board(self, token: str, profile: SearchProfile) -> list[RawPosting]:
        listing = self.client.get_json(self.board_url(token), params={"limit": 100})
        raws = self.parse_board(listing, token)
        out: list[RawPosting] = []
        for r in raws:
            if not title_matches(normalize_title(r.title)[0], profile.title_terms):
                continue
            ref = r.extra.get("ref")
            if not ref:
                # No documented detail endpoint for this job — keep the listing-level
                # posting (title/location/guess_url) and let hydration fetch the body.
                out.append(r)
                continue
            try:
                detail = self.client.get_json(ref)
            except (RateLimited, BudgetExceeded):
                raise
            except SourceError:
                out.append(r)
                continue
            job_ad = detail.get("jobAd") or {}
            parts = []
            for key, heading in (
                ("jobDescription", "Job Description"),
                ("qualifications", "Qualifications"),
                ("additionalInformation", "Additional Information"),
                ("companyDescription", "About the Company"),
            ):
                text = job_ad.get(key)
                if text:
                    parts.append(f"<h3>{heading}</h3>{text}")
            r.description = "".join(parts)
            r.description_is_html = True
            r.description_complete = bool(parts)
            r.url = detail.get("postingUrl") or r.url
            r.apply_url = detail.get("applyUrl") or detail.get("postingUrl") or r.apply_url
            out.append(r)
        return out

    def parse_board(self, payload: Any, token: str) -> list[RawPosting]:
        raws: list[RawPosting] = []
        for job in payload.get("content", []):
            try:
                loc = job.get("location") or {}
                parts = [loc.get("city"), loc.get("region"), loc.get("country")]
                released = job.get("releasedDate")
                # api-notes.md 2026-09-04: `url`/`apply_url` are NOT a documented field. The
                # only documented job-detail URL in the list response is `ref` — but that is
                # the *API* endpoint, not something a human can open, so it stays in
                # extra["ref"] and is used solely by `fetch_board` to fetch details. The
                # listing-level url/apply_url default to the guessed public-site pattern;
                # `fetch_board` overwrites both with the detail response's real `postingUrl`/
                # `applyUrl` once it has fetched it.
                ref = job.get("ref")
                guess_url = f"https://jobs.smartrecruiters.com/{token}/{job['id']}"
                raws.append(RawPosting(
                    source=self.name, source_id=str(job["id"]),
                    url=guess_url,
                    apply_url=guess_url,
                    title=job["name"], company_name=token,
                    location_raw=", ".join(p for p in parts if p) or None,
                    remote_hint="remote" if loc.get("remote") else None,
                    description="", description_complete=False,
                    posted_at=(
                        datetime.fromisoformat(released.replace("Z", "+00:00"))
                        if released else None
                    ),
                    country_hint=(loc.get("country") or "").upper() or None,
                    extra={"ref": ref, "guess_url": guess_url},
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return raws
