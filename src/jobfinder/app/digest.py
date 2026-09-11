from __future__ import annotations

import logging
import smtplib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from jobfinder.app.deps import templates
from jobfinder.config import Profile
from jobfinder.db.models import Contact, Document, Posting, Run, Score, SpendLedger, utcnow
from jobfinder.db.session import release_snapshot
from jobfinder.settings import Settings

log = logging.getLogger(__name__)
MAX_MATCHES = 10


@dataclass
class DigestData:
    since: datetime
    base_url: str
    counts: dict = field(default_factory=dict)
    matches: list[dict] = field(default_factory=list)
    contacts: list[dict] = field(default_factory=list)
    docs_ready: list[dict] = field(default_factory=list)
    errors: dict = field(default_factory=dict)
    spend_mtd: float = 0.0


class Sender(Protocol):
    def send(self, to: str, subject: str, html: str, text: str) -> None: ...


class GmailSender:
    def __init__(self, user: str, app_password: str) -> None:
        self.user, self.app_password = user, app_password

    def send(self, to: str, subject: str, html: str, text: str) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.user, to, subject
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(self.user, self.app_password)
            smtp.send_message(msg)


def last_successful_digest(session: Session) -> datetime | None:
    return session.scalar(
        select(func.max(Run.finished_at)).where(Run.kind == "digest", Run.status == "ok")
    )


def _naive_now() -> datetime:
    return utcnow().replace(tzinfo=None)


def build_digest(
    session: Session,
    *,
    profile: Profile,
    since: datetime | None = None,
    limit: int | None = MAX_MATCHES,
) -> DigestData:
    """What the brief would carry since `since` (default: the last sent brief). `limit=None`
    lists every match — the dashboard preview; the email keeps the top MAX_MATCHES."""
    since = since or last_successful_digest(session) or (_naive_now() - timedelta(days=1))
    base = profile.dashboard.public_base_url.rstrip("/")
    data = DigestData(since=since, base_url=base)
    new_postings = (
        session.scalar(select(func.count(Posting.id)).where(Posting.first_seen_at >= since)) or 0
    )
    # A posting is news the day it *became* a match, not the day it was first seen:
    # hydration and scoring are capped per run, so day-0 arrivals are often scored on
    # day 3, and keying on first_seen_at would leave them out of every brief. The first
    # score at or above the threshold is that moment; a later re-score does not repeat it.
    first_match = (
        select(Score.posting_id, func.min(Score.created_at).label("matched_at"))
        .where(Score.fit_score >= profile.scoring.match_threshold)
        .group_by(Score.posting_id)
        .subquery()
    )
    matches = session.scalars(
        select(Posting)
        .join(first_match, first_match.c.posting_id == Posting.id)
        .where(Posting.status == "match", first_match.c.matched_at >= since)
    ).all()
    matches.sort(key=lambda p: -(p.latest_score.fit_score if p.latest_score else 0))
    for p in matches[:limit] if limit else matches:
        s = p.latest_score
        data.matches.append({
            "id": p.id, "title": p.title, "company": p.company.name,
            "score": s.fit_score if s else None,
            "where": " ".join(x for x in [p.city, p.country, p.remote_type] if x),
            "summary": s.one_line_summary if s else "", "url": f"{base}/jobs/{p.id}",
        })
    contacts = session.scalars(select(Contact).where(Contact.created_at >= since)).all()
    for c in contacts:
        ev = c.evidence or {}
        label = c.full_name or (
            "manual lookup needed" if ev.get("needs_manual") else "switchboard only"
        )
        data.contacts.append({
            "name": label, "title": c.title or "",
            "company": c.posting.company.name, "email": c.email, "phone": c.phone,
            "confidence": c.confidence, "url": f"{base}/jobs/{c.posting_id}",
        })
    docs = session.scalars(
        select(Document).where(
            Document.created_at >= since, Document.status == "ready",
            Document.kind == "resume", Document.format == "docx",
        )
    ).all()
    for d in docs:
        data.docs_ready.append({
            "company": d.posting.company.name, "title": d.posting.title, "ats": d.ats_score,
            "url": f"{base}/jobs/{d.posting_id}",
        })
    last_scan = session.scalar(
        select(Run).where(Run.kind == "scan").order_by(Run.started_at.desc()).limit(1)
    )
    scan_stats = (last_scan.stats or {}) if last_scan else {}
    data.errors = dict(scan_stats.get("errors", {}))
    month_start = datetime.now(UTC).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    spend = session.scalar(
        select(func.coalesce(func.sum(SpendLedger.usd_estimate), 0.0)).where(
            SpendLedger.occurred_at >= month_start
        )
    )
    data.spend_mtd = float(spend or 0.0)
    scoring = scan_stats.get("scoring", {}) or {}
    data.counts = {
        "new_postings": new_postings, "matches": len(matches), "contacts": len(contacts),
        "docs_ready": len(docs), "scan_sources": scan_stats.get("sources", {}),
        # What the last scan's scoring pass did, so a zero-match morning is explainable
        # at a glance ("scored 60, 0 matched" vs "scored 0").
        "scored": int(scoring.get("scored", 0) or 0),
        "scan_matches": int(scoring.get("matches", 0) or 0),
        "match_threshold": profile.scoring.match_threshold,
    }
    return data


def render_digest(data: DigestData) -> tuple[str, str, str]:
    n = data.counts.get("matches", 0)
    subject = (
        f"jobfinder: {n} match{'es' if n != 1 else ''}, {data.counts.get('contacts', 0)} contacts, "
        f"{data.counts.get('docs_ready', 0)} docs ready"
    )
    html = templates.get_template("digest.html").render(d=data, subject=subject)
    text = templates.get_template("digest.txt").render(d=data, subject=subject)
    return subject, html, text


def send_digest(
    session: Session,
    *,
    settings: Settings,
    profile: Profile,
    sender: Sender | None = None,
    retry_delay_s: int = 300,
) -> Run:
    run = Run(kind="digest")
    session.add(run)
    session.commit()
    if not settings.has("gmail_user", "gmail_app_password", "digest_to"):
        run.status = "error"
        run.stats = {"error": "GMAIL_USER / GMAIL_APP_PASSWORD / DIGEST_TO missing"}
        run.finished_at = _naive_now()
        session.commit()
        return run
    sender = sender or GmailSender(settings.gmail_user, settings.gmail_app_password)
    data = build_digest(session, profile=profile)
    subject, html, text = render_digest(data)
    # The SMTP call (30 s timeout) and the retry sleep must not sit on the read snapshot
    # build_digest opened: any commit elsewhere (the scan thread) would make the status
    # write below fail with "database is locked" (2026-09-09 07:05).
    release_snapshot(session)
    attempts, error = 0, ""
    for attempt in (1, 2):
        attempts = attempt
        try:
            sender.send(settings.digest_to, subject, html, text)
            error = ""
            break
        except Exception as exc:  # noqa: BLE001 — retry once, then record
            error = repr(exc)
            log.warning("digest send failed (attempt %s): %s", attempt, exc)
            if attempt == 1 and retry_delay_s:
                time.sleep(retry_delay_s)
    stats = {
        "attempts": attempts, "counts": data.counts, "error": error, "to": settings.digest_to,
    }
    return _finish(session, run.id, status="ok" if not error else "error", stats=stats)


def _finish(session: Session, run_id: int, *, status: str, stats: dict) -> Run:
    """Write the run's final status; on a stale snapshot, roll back and write once more so
    the row never stays `running`."""
    for attempt in (1, 2):
        run = session.get(Run, run_id)
        run.finished_at = _naive_now()
        run.status, run.stats = status, stats
        try:
            session.commit()
            return run
        except OperationalError:
            session.rollback()
            if attempt == 2:
                raise
    raise AssertionError("unreachable")
