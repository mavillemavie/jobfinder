"""Add a job by hand from its posting URL (dashboard "Add job", `jobfinder add`)."""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from jobfinder.db.models import Posting, PostingSource
from jobfinder.discovery.base import RawPosting, SourceError
from jobfinder.discovery.dedupe import content_hash, upsert_one
from jobfinder.discovery.fetch import HttpClient, extract_description, html_to_text, redact_url
from jobfinder.discovery.hydrate import fetch_url_for
from jobfinder.discovery.normalize import detect_language
from jobfinder.llm.base import LLMError, LLMProvider, load_prompt, load_schema

log = logging.getLogger(__name__)

# Query parameters that only track the click; dropping them makes the same posting pasted
# twice (from an email, then from the board) one URL.
_TRACKING = {"trk", "trkinfo", "refid", "trackingid", "gh_src", "lipi", "src"}


def canonical_url(url: str) -> str:
    parts = urlparse((url or "").strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("not an http(s) URL")
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not (k.lower().startswith("utm_") or k.lower() in _TRACKING)
    ]
    return urlunparse(parts._replace(query=urlencode(query), fragment=""))


def _jobposting_nodes(data: Any) -> Iterator[dict]:
    if isinstance(data, list):
        for item in data:
            yield from _jobposting_nodes(item)
    elif isinstance(data, dict):
        kind = data.get("@type")
        if "JobPosting" in (kind if isinstance(kind, list) else [kind]):
            yield data
        if "@graph" in data:
            yield from _jobposting_nodes(data["@graph"])


def _org_name(org: Any) -> str:
    if isinstance(org, list):
        org = org[0] if org else None
    if isinstance(org, dict):
        org = org.get("name")
    return str(org or "").strip()


def _location(node: dict) -> str | None:
    remote = str(node.get("jobLocationType") or "").upper() == "TELECOMMUTE"
    locs = node.get("jobLocation")
    locs = locs if isinstance(locs, list) else [locs] if locs else []
    parts: list[str] = []
    addr = locs[0].get("address") if locs and isinstance(locs[0], dict) else None
    if isinstance(addr, dict):
        country = addr.get("addressCountry")
        if isinstance(country, dict):
            country = country.get("name")
        parts = [
            str(x).strip()
            for x in (addr.get("addressLocality"), addr.get("addressRegion"), country)
            if x and str(x).strip()
        ]
    elif isinstance(addr, str) and addr.strip():
        parts = [addr.strip()]
    text = ", ".join(parts)
    if remote:
        return f"Remote ({text})" if text else "Remote"
    return text or None


def _posted(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _description(value: Any) -> str:
    desc = str(value or "")
    # Many boards HTML-escape the markup inside the JSON string.
    if "&lt;" in desc:
        desc = html_lib.unescape(desc)
    return html_to_text(desc) if desc.strip() else ""


def parse_jsonld(html: str) -> dict[str, Any] | None:
    """The first schema.org JobPosting embedded in the page, or None."""
    if not html:
        return None
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except ValueError:
            continue
        node = next(_jobposting_nodes(data), None)
        if node is None:
            continue
        return {
            "title": str(node.get("title") or "").strip(),
            "company": _org_name(node.get("hiringOrganization")),
            "location": _location(node),
            "description": _description(node.get("description")),
            "posted_at": _posted(node.get("datePosted")) if node.get("datePosted") else None,
        }
    return None


THIN_CHARS = 300  # below this the description is a teaser, not a posting


@dataclass
class ParsedJob:
    url: str
    title: str
    company: str
    location: str | None
    description: str
    posted_at: datetime | None
    origin: str  # "jsonld" | "llm" | "none" (fields came from JF, or nowhere)
    fetch_error: str | None  # "403", "429", "SourceError", ...; None when the page loaded
    thin: bool


def _page_title(html: str) -> str:
    if not html:
        return ""
    node = BeautifulSoup(html, "lxml").title
    return node.get_text(strip=True) if node else ""


def _llm_extract(llm: LLMProvider, text: str, page_title: str) -> dict[str, str] | None:
    try:
        return llm.complete_json(
            task="extract_posting",
            system=load_prompt("extract_posting"),
            user=f"PAGE TITLE: {page_title}\n\nPAGE TEXT:\n{text[:8000]}",
            schema=load_schema("extract_posting"),
            tier="fast",
        )
    except LLMError as exc:
        log.warning("add: posting extraction failed: %s", exc)
        return None


def parse_url(url: str, *, text: str | None, client: HttpClient, llm: LLMProvider) -> ParsedJob:
    """Read a posting page into editable fields. Raises ValueError only for a bad URL."""
    url = canonical_url(url)
    page, fetch_error = "", None
    try:
        page = client.get_text(fetch_url_for(url))
    except SourceError as exc:
        msg = str(exc)
        fetch_error = msg[:3] if msg[:3].isdigit() else type(exc).__name__
        log.warning("add: fetch failed for %s: %s", redact_url(url), fetch_error)
    fields = parse_jsonld(page) or {
        "title": "", "company": "", "location": None, "description": "", "posted_at": None,
    }
    origin = "jsonld" if fields["title"] and fields["company"] else "none"
    # LinkedIn's guest endpoint is read against the view URL so its selector applies.
    description = fields["description"] or (extract_description(page, url) if page else "")
    pasted = (text or "").strip()
    if len(pasted) > len(description):
        description = pasted
    # The LLM reads the whole page: title, company and location usually sit in a header outside
    # the description element (Greenhouse job-boards, LinkedIn guest pages carry no JSON-LD).
    page_text = html_to_text(page) if page else ""
    llm_text = "\n\n".join(
        # Both halves survive _llm_extract's 8000-char cut: the header leads the page text,
        # the pasted text leads with the job when the page was a login wall.
        t for t in (page_text[:5000] if len(page_text) >= THIN_CHARS else "", pasted[:3000]) if t
    ) or description
    if origin == "none" and llm_text:
        got = _llm_extract(llm, llm_text, _page_title(page))
        if got:
            fields["title"] = fields["title"] or got["title"].strip()
            fields["company"] = fields["company"] or got["company"].strip()
            fields["location"] = fields["location"] or got["location"].strip() or None
            if fields["title"] and fields["company"]:
                origin = "llm"
    return ParsedJob(
        url=url,
        title=fields["title"],
        company=fields["company"],
        location=fields["location"],
        description=description,
        posted_at=fields["posted_at"],
        origin=origin,
        fetch_error=fetch_error,
        thin=len(description) < THIN_CHARS,
    )


# Manual adds are an explicit choice: an old datePosted never makes one stale.
_NEVER_STALE_DAYS = 36500
_REOPENABLE = {None, "new"}


def find_by_url(session: Session, url: str) -> Posting | None:
    """A posting already holding this exact URL (as a source or as its apply link)."""
    via_source = session.scalar(select(PostingSource).where(PostingSource.url == url))
    if via_source is not None:
        return via_source.posting
    return session.scalar(select(Posting).where(Posting.apply_url == url))


def _source_id(url: str) -> str:
    # posting_sources.source_id is 255 chars; a cut-off URL could merge two different postings.
    return url if len(url) <= 255 else "sha1:" + hashlib.sha1(url.encode()).hexdigest()


def _merge_into(session: Session, posting: Posting, raw: RawPosting) -> None:
    """Attach a manual add to the posting already holding its URL. Like a second source in the
    scan's upsert, it never rewrites the identity fields; a longer description replaces a
    snippet."""
    if not any(s.source == raw.source and s.source_id == raw.source_id for s in posting.sources):
        posting.sources.append(
            PostingSource(source=raw.source, source_id=raw.source_id, url=raw.url)
        )
    text = raw.description.strip()[:20000]
    if text and len(text) > len(posting.description_text or ""):
        posting.description_text = text
        posting.content_hash = content_hash(text)
        posting.language = detect_language(text)
        posting.description_complete = raw.description_complete
    session.flush()


def save_manual(
    session: Session, parsed: ParsedJob, *, match_threshold: int | None = None
) -> tuple[Posting, bool]:
    """Put a hand-added job on the Shortlist: the posting already holding its URL when there is
    one (what the preview showed), else the scan's dedupe upsert. Commits.

    `match_threshold` restores match/scored for a reopened posting that already has a score;
    without it the posting goes back to "new" and the next scan re-scores it."""
    from jobfinder.app.routes.inbox import shortlist_posting

    title, company = parsed.title.strip(), parsed.company.strip()
    if not title or not company:
        raise ValueError("title and company are required")
    raw = RawPosting(
        source="manual",
        source_id=_source_id(parsed.url),
        url=parsed.url,
        title=title,
        company_name=company,
        apply_url=parsed.url,
        location_raw=parsed.location or None,
        description=parsed.description,
        posted_at=parsed.posted_at,
        # A teaser stays incomplete so the scan's hydration can still fetch the full text.
        description_complete=bool(parsed.description.strip()) and not parsed.thin,
    )
    existing = find_by_url(session, parsed.url)
    if existing is not None:
        posting, created = existing, False
        _merge_into(session, posting, raw)
    else:
        posting, created = upsert_one(session, raw, max_age_days=_NEVER_STALE_DAYS)
    if posting.status in ("prefiltered_out", "dismissed"):
        score = posting.latest_score
        if score is not None and match_threshold is not None:
            posting.status = "match" if score.fit_score >= match_threshold else "scored"
        else:
            posting.status = "new"
        posting.dismiss_reason = None
    posting.prefilter_result = {
        **(posting.prefilter_result or {}), "passed": True, "reason": "manual", "manual": True,
    }
    pl = posting.pipeline
    stage = pl.stage if pl is not None else None
    if stage in _REOPENABLE or (stage == "closed" and pl.close_reason == "withdrawn"):
        if pl is not None:
            pl.close_reason = None
        shortlist_posting(session, posting)
    session.commit()
    log.info("add: posting %s from %s (created=%s)", posting.id, redact_url(parsed.url), created)
    return posting, created
