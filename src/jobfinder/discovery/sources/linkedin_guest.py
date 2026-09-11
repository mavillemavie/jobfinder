from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from bs4 import BeautifulSoup

from jobfinder.discovery.base import LocationQuery, RawPosting, SearchProfile
from jobfinder.discovery.fetch import BudgetExceeded, HttpClient

log = logging.getLogger(__name__)

SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
_ID_RE = re.compile(r"-(\d{6,})(?:\?|$)")


class LinkedInGuestSource:
    name = "linkedin_guest"

    def __init__(self, client: HttpClient) -> None:
        self.client = client

    def fetch(self, profile: SearchProfile, since: datetime) -> list[RawPosting]:
        out: list[RawPosting] = []
        for q in profile.queries_for_country("CA", "GB"):
            for keyword in profile.search_keywords[:2]:
                params = {
                    "keywords": keyword,
                    "location": q.where or profile.country_name(q.country),
                    "f_TPR": "r604800",
                    "start": 0,
                }
                try:
                    html = self.client.get_text(SEARCH, params=params)
                except BudgetExceeded:
                    return out
                out.extend(self.parse(html, q))
        return out

    def parse(self, html: str, query: LocationQuery) -> list[RawPosting]:
        raws: list[RawPosting] = []
        soup = BeautifulSoup(html, "lxml")
        cards = soup.select("div.base-card")
        if not cards:
            log.warning(
                "linkedin_guest: no job cards for %s (%s chars) — possible block page",
                query.key,
                len(html),
            )
        for card in cards:
            try:
                link = card.select_one("a.base-card__full-link")
                href = (link["href"] if link else "").split("?")[0]
                urn = card.get("data-entity-urn", "")
                m = re.search(r"jobPosting:(\d+)", urn) or _ID_RE.search(href)
                if not m or not href:
                    continue
                title_el = card.select_one("h3.base-search-card__title") or link
                company_el = card.select_one("h4.base-search-card__subtitle")
                loc_el = card.select_one("span.job-search-card__location")
                time_el = card.select_one("time")
                loc_text = loc_el.get_text(strip=True) if loc_el else None
                remote_hint = None
                if loc_text and "(" in loc_text:
                    tag = loc_text[loc_text.rfind("(") + 1 : loc_text.rfind(")")].lower()
                    remote_hint = tag if tag in ("remote", "hybrid", "on-site") else None
                    if remote_hint == "on-site":
                        remote_hint = "onsite"
                    loc_text = loc_text[: loc_text.rfind("(")].strip()
                posted = None
                if time_el and time_el.get("datetime"):
                    posted = datetime.fromisoformat(time_el["datetime"]).replace(tzinfo=UTC)
                company_name = (
                    company_el.get_text(strip=True)
                    if company_el
                    else "Unknown"
                )
                raws.append(RawPosting(
                    source=self.name,
                    source_id=m.group(1),
                    url=href,
                    apply_url=href,
                    title=title_el.get_text(strip=True),
                    company_name=company_name,
                    location_raw=loc_text,
                    remote_hint=remote_hint,
                    description="",
                    description_complete=False,
                    posted_at=posted,
                    country_hint=query.country,
                    location_key=query.key,
                ))
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
        return raws
