from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobfinder.db.models import Company, Posting, PostingSource, utcnow
from jobfinder.discovery.base import RawPosting
from jobfinder.discovery.fetch import html_to_text
from jobfinder.discovery.normalize import (
    detect_ats,
    detect_language,
    normalize_company,
    normalize_title,
    parse_location,
)
from jobfinder.discovery.twins import blocking_twin, inherit_dismissal

log = logging.getLogger(__name__)


@dataclass
class UpsertStats:
    new: int = 0
    updated: int = 0
    stale: int = 0
    errors: int = 0
    dismissed_twin: int = 0  # new rows of a job JF already rejected (inserted as dismissed)


def make_dedupe_key(company_norm: str, title_norm: str, place: str) -> str:
    return hashlib.sha1(f"{company_norm}|{title_norm}|{place}".encode()).hexdigest()


def content_hash(text: str) -> str:
    return hashlib.sha1(" ".join(text.split()).encode()).hexdigest()


def get_or_create_company(
    session: Session, name: str, ats: tuple[str, str] | None = None, country: str | None = None
) -> Company:
    norm = normalize_company(name) or name.lower().strip()
    company = session.scalar(select(Company).where(Company.normalized_name == norm))
    if company is None:
        company = Company(name=name.strip(), normalized_name=norm, country=country)
        session.add(company)
        session.flush()
    if ats and not company.ats_type:
        company.ats_type, company.ats_board_token = ats
    return company


def _as_naive_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt


def upsert_raw_postings(
    session: Session, raws: list[RawPosting], *, max_age_days: int
) -> UpsertStats:
    stats = UpsertStats()
    cutoff = utcnow().replace(tzinfo=None) - timedelta(days=max_age_days)
    for raw in raws:
        try:
            with session.begin_nested():
                _upsert_one(session, raw, cutoff, stats)
        except Exception:  # noqa: BLE001 — one bad item never aborts the batch
            log.exception("upsert failed for %s:%s", raw.source, raw.source_id)
            stats.errors += 1
    session.commit()
    return stats


def _upsert_one(session: Session, raw: RawPosting, cutoff: datetime, stats: UpsertStats) -> None:
    ats = detect_ats(raw.apply_url) or detect_ats(raw.url)
    for link in raw.extra.get("apply_links", []):
        ats = ats or detect_ats(link)
    title_norm, seniority = normalize_title(raw.title)
    loc = parse_location(raw.location_raw, raw.remote_hint, raw.country_hint)
    company = get_or_create_company(session, raw.company_name or "Unknown", ats, loc.country)
    place = loc.city or (
        loc.remote_scope or "remote" if loc.remote_type == "remote" else loc.country or "unknown"
    )
    key = make_dedupe_key(company.normalized_name, title_norm, place.lower())
    text = html_to_text(raw.description) if raw.description_is_html else raw.description.strip()
    text = text[:20000]
    chash = content_hash(text)
    now = utcnow().replace(tzinfo=None)

    existing = session.scalar(select(Posting).where(Posting.dedupe_key == key))
    prior = session.scalar(
        select(PostingSource).where(
            PostingSource.source == raw.source,
            PostingSource.source_id == raw.source_id,
        )
    )
    if existing is None and prior is not None:
        # The dedupe key is company|normalized_title|place, so an employer editing the title
        # ("Data Analyst" → "Data Analyst, Marketing") mints a *new* key for a posting we
        # already hold. Without this lookup the insert below would violate the
        # (source, source_id) unique constraint and the posting could never ingest again.
        existing = prior.posting
        existing.dedupe_key = key
    if prior is not None and existing is not None and prior.posting_id == existing.id:
        # The same listing re-read from the same source is authoritative for its own
        # identity fields — refresh them so a retitle also fixes a now-stale seniority
        # (which the prefilter gates on). A *different* source merging into this posting
        # never overwrites them.
        existing.title = raw.title.strip()[:300]
        existing.normalized_title = title_norm[:300]
        existing.seniority = seniority
        existing.location_raw = (raw.location_raw or "")[:300] or None
        existing.country, existing.region, existing.city = loc.country, loc.region, loc.city
        existing.remote_type, existing.remote_scope = loc.remote_type, loc.remote_scope

    if existing is not None:
        existing.last_seen_at = now
        has_source = any(
            s.source == raw.source and s.source_id == raw.source_id for s in existing.sources
        )
        if not has_source:
            existing.sources.append(
                PostingSource(source=raw.source, source_id=raw.source_id, url=raw.url)
            )
        changed = chash != existing.content_hash
        should_overwrite = (
            raw.description_complete
            and bool(text)
            and (
                not existing.description_complete
                or len(text) >= len(existing.description_text)
            )
        )
        if changed and should_overwrite:
            if existing.status in ("scored", "match"):
                existing.prefilter_result = {**(existing.prefilter_result or {}), "rescore": True}
            existing.description_text = text
            existing.description_complete = True
            existing.content_hash = chash
            existing.language = detect_language(text)
        session.flush()
        stats.updated += 1
        return

    posted = _as_naive_utc(raw.posted_at)
    posting = Posting(
        company_id=company.id,
        title=raw.title.strip()[:300],
        normalized_title=title_norm[:300],
        seniority=seniority,
        location_raw=(raw.location_raw or "")[:300] or None,
        country=loc.country,
        region=loc.region,
        city=loc.city,
        remote_type=loc.remote_type,
        remote_scope=loc.remote_scope,
        language=detect_language(text) if text else "en",
        description_text=text,
        description_complete=raw.description_complete and bool(text),
        salary_raw=raw.salary_raw,
        posted_at=posted,
        first_seen_at=now,
        last_seen_at=now,
        apply_url=(raw.apply_url or raw.url)[:1000],
        dedupe_key=key,
        content_hash=chash,
        status="new",
    )
    if posted is not None and posted < cutoff:
        posting.status = "prefiltered_out"
        posting.prefilter_result = {"passed": False, "reason": "stale"}
        stats.stale += 1
    else:
        twin = blocking_twin(session, company.id, title_norm[:300])
        if twin is not None:
            # Same employer, same title, another place or source: JF already said no.
            inherit_dismissal(posting, twin)
            stats.dismissed_twin += 1
    posting.sources.append(PostingSource(source=raw.source, source_id=raw.source_id, url=raw.url))
    session.add(posting)
    session.flush()
    stats.new += 1
