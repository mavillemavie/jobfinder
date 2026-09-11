from __future__ import annotations

import calendar
import html
import logging
import re
from datetime import UTC, datetime
from typing import Any

import feedparser

from jobfinder.discovery.base import (
    LocationQuery,
    RawPosting,
    SearchProfile,
    SourceError,
    filter_by_title,
)
from jobfinder.discovery.fetch import BudgetExceeded, HttpClient, RateLimited

log = logging.getLogger(__name__)

JOBBANK_SEARCH = "https://www.jobbank.gc.ca/jobsearch/jobsearch"
JOBBANK_HOST = "https://www.jobbank.gc.ca"
REMOTIVE_API = "https://remotive.com/api/remote-jobs"
WWR_FEED = "https://weworkremotely.com/remote-jobs.rss"

_FEED_LINK_RE = re.compile(r'href="([^"]*jobSearchRSSfeed[^"]*)"')


def _entry_time(entry: Any) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC) if parsed else None


def _field(text: str, *labels: str) -> str | None:
    # api-notes.md's observed shape wraps the label itself in <strong>...</strong>, so the
    # colon is immediately followed by a closing tag, not the value — skip any HTML tags
    # between the colon and the value rather than assuming plain "Label: value" text.
    for label in labels:
        m = re.search(rf"{label}\s*:\s*(?:</?[a-zA-Z][^>]*>\s*)*([^<\n]+)", text, re.IGNORECASE)
        if m:
            return html.unescape(m.group(1)).strip()
    return None


class JobBankRssSource:
    """Job Bank Canada — fragile, unofficial two-step scrape (api-notes.md, 2026-09-04).

    No official API exists for this feed; it's a byproduct of the human search UI. The brief's
    original single-request RSS URL returns HTTP 404 (confirmed live, twice). The real, working
    shape is two requests bound by one session:
      1. GET the human search page (`jobsearch/jobsearch`) — this sets a JSESSIONID cookie and
         embeds the feed URL, which includes server-selected `fcid`/`fn21` taxonomy ids that
         cannot be constructed standalone.
      2. GET that embedded feed URL, reusing the same session cookie. `HttpClient` wraps a
         single `httpx.Client` instance per adapter call, and `httpx.Client` keeps cookies
         across requests automatically, so no extra cookie-jar plumbing is needed here as long
         as both requests go through the same `HttpClient`.
    The response is an Atom feed (`<feed><entry>`), not RSS 2.0, despite the "RSS" name. This
    can break at any time if Job Bank changes its search page markup or session mechanism —
    there is no SLA and no official docs. If step 1 doesn't yield a usable feed link (or either
    step raises), `fetch` logs a warning and moves on to the next query rather than raising.
    """

    name = "jobbank_rss"

    def __init__(self, client: HttpClient) -> None:
        self.client = client

    def fetch(self, profile: SearchProfile, since: datetime) -> list[RawPosting]:
        out: list[RawPosting] = []
        for q in profile.queries_for_country("CA"):
            for keyword in profile.search_keywords:
                params = {"searchstring": keyword, "locationstring": q.where or "", "sort": "M"}
                try:
                    session_html = self.client.get_text(JOBBANK_SEARCH, params=params)
                except BudgetExceeded:
                    return out
                except RateLimited:
                    # A 429 is an adapter-wide condition, not one bad keyword: let it out
                    # so run_scan records the cooldown. (BudgetExceeded keeps its
                    # partial-results `return out` in the handler above.)
                    raise
                except SourceError as exc:
                    log.warning("jobbank_rss: session request failed for %r: %s", keyword, exc)
                    continue
                m = _FEED_LINK_RE.search(session_html)
                if not m:
                    log.warning("jobbank_rss: no feed link found in session page for %r", keyword)
                    continue
                feed_url = html.unescape(m.group(1))
                if feed_url.startswith("/"):
                    feed_url = JOBBANK_HOST + feed_url
                try:
                    xml = self.client.get_text(feed_url)
                except BudgetExceeded:
                    return out
                except RateLimited:
                    raise
                except SourceError as exc:
                    log.warning("jobbank_rss: feed request failed for %r: %s", keyword, exc)
                    continue
                out.extend(self.parse(xml, q))
        return out

    def parse(self, xml: str, query: LocationQuery) -> list[RawPosting]:
        parsed = feedparser.parse(xml)
        if not parsed.entries and (parsed.bozo or "<feed" not in xml[:4000]):
            log.warning(
                "jobbank: feed response is not an Atom feed for %s (%s chars, bozo=%s)",
                query.key, len(xml), bool(parsed.bozo),
            )
            return []

        raws: list[RawPosting] = []
        for e in parsed.entries:
            try:
                link = e.get("link", "")
                m = re.search(r"/jobposting/(\d+)", link)
                if not m:
                    continue
                summary = e.get("summary", "") or e.get("description", "")
                raws.append(RawPosting(
                    source=self.name, source_id=m.group(1), url=link.split("?")[0],
                    apply_url=link.split("?")[0],
                    title=(e.get("title", "") or "").strip(),
                    company_name=_field(summary, "Employer", "Employeur") or "Unknown (Job Bank)",
                    location_raw=_field(summary, "Location", "Lieu") or query.where,
                    description=summary, description_is_html=True, description_complete=False,
                    salary_raw=_field(summary, "Salary", "Salaire"), posted_at=_entry_time(e),
                    country_hint="CA", location_key=query.key,
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return raws


class RemotiveSource:
    name = "remotive"

    def __init__(self, client: HttpClient) -> None:
        self.client = client

    def fetch(self, profile: SearchProfile, since: datetime) -> list[RawPosting]:
        out: list[RawPosting] = []
        for keyword in profile.search_keywords:
            try:
                payload = self.client.get_json(
                    REMOTIVE_API, params={"search": keyword, "limit": 100}
                )
            except BudgetExceeded:
                return out
            out.extend(self.parse(payload))
        return out

    def parse(self, payload: dict[str, Any]) -> list[RawPosting]:
        raws: list[RawPosting] = []
        for item in payload.get("jobs", []):
            try:
                pub = item.get("publication_date")
                raws.append(RawPosting(
                    source=self.name, source_id=str(item["id"]), url=item["url"],
                    apply_url=item["url"],
                    title=item["title"], company_name=item.get("company_name") or "Unknown",
                    location_raw=item.get("candidate_required_location") or "Worldwide",
                    remote_hint="remote", description=item.get("description", ""),
                    description_is_html=True, description_complete=True,
                    salary_raw=item.get("salary") or None,
                    posted_at=datetime.fromisoformat(pub).replace(tzinfo=UTC) if pub else None,
                    location_key="remote-from-canada",
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return raws


class WeWorkRemotelySource:
    name = "weworkremotely"

    def __init__(self, client: HttpClient) -> None:
        self.client = client

    def fetch(self, profile: SearchProfile, since: datetime) -> list[RawPosting]:
        try:
            xml = self.client.get_text(WWR_FEED)
        except BudgetExceeded:
            return []
        return filter_by_title(self.parse(xml), profile)

    def parse(self, xml: str) -> list[RawPosting]:
        raws: list[RawPosting] = []
        for e in feedparser.parse(xml).entries:
            try:
                full = e.get("title", "")
                company, _, title = full.partition(": ")
                if not title:
                    company, title = "Unknown", full
                link = e.get("link", "")
                raws.append(RawPosting(
                    source=self.name, source_id=link.rstrip("/").rsplit("/", 1)[-1], url=link,
                    apply_url=link,
                    title=title.strip(), company_name=company.strip(),
                    location_raw=e.get("region") or "Anywhere in the World",
                    remote_hint="remote",
                    description=e.get("summary", "") or e.get("description", ""),
                    description_is_html=True,
                    description_complete=True, posted_at=_entry_time(e),
                    location_key="remote-from-canada",
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return raws
