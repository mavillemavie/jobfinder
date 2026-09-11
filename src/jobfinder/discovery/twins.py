"""A rejected job must not come back as a different row.

The dedupe key is company | normalized_title | place, so the same job in another city (or
with a title variant from a second source) mints a new row. This module holds the one rule
for "the same job" — same company, exactly equal normalized title — and the scope each
rejection reason carries. Spec: docs/specs/2026-09-06-rejected-twins-design.md.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobfinder.db.models import Pipeline, Posting

# Reasons not listed here block the same company + title.
_SCOPE = {"bad_company": "company", "wrong_location": "row"}
LIVE_STATUSES = ("new", "scored", "match")


def scope_for(reason: str | None) -> str:
    """'company' blocks every title from the employer, 'title' the same normalized title,
    'row' nothing beyond the posting itself (the place is what differs between twins)."""
    return _SCOPE.get(reason or "", "title")


def rejection_of(posting: Posting) -> tuple[str, str] | None:
    """(reason, scope) if this posting blocks twins, else None. A pipeline closed as
    `rejected` (the employer said no) counts like a dismissal with title scope."""
    if posting.status == "dismissed":
        scope = scope_for(posting.dismiss_reason)
        return None if scope == "row" else (posting.dismiss_reason or "other", scope)
    pl = posting.pipeline
    if pl is not None and pl.stage == "closed" and pl.close_reason == "rejected":
        return ("rejected", "title")
    return None


def blocking_twin(session: Session, company_id: int, normalized_title: str) -> Posting | None:
    """The rejected posting, if any, that makes a (company, title) unwanted."""
    rows = session.scalars(
        select(Posting)
        .outerjoin(Pipeline)
        .where(Posting.company_id == company_id)
        .where(
            (Posting.status == "dismissed")
            | ((Pipeline.stage == "closed") & (Pipeline.close_reason == "rejected"))
        )
        .order_by(Posting.id)
    ).all()
    for p in rows:
        rej = rejection_of(p)
        if rej is None:
            continue
        if rej[1] == "company" or p.normalized_title == normalized_title:
            return p
    return None


def inherit_dismissal(posting: Posting, twin: Posting) -> None:
    """Mark a new row as the twin of an already rejected posting."""
    reason, _scope = rejection_of(twin) or ("other", "title")
    posting.status = "dismissed"
    posting.dismiss_reason = reason
    posting.prefilter_result = {"passed": False, "reason": "dismissed_twin", "twin_id": twin.id}


def sweep_twins(session: Session, posting: Posting, reason: str, scope: str) -> list[Posting]:
    """Dismiss the live siblings a rejection covers (same company; same title unless the scope
    is the whole company). Pipeline closing and the activity log are the caller's job, so
    this module stays free of the dashboard's stage bookkeeping. Returns the swept rows."""
    if scope == "row":
        return []
    stmt = select(Posting).where(
        Posting.company_id == posting.company_id,
        Posting.id != posting.id,
        Posting.status.in_(LIVE_STATUSES),
        # A row JF already acted on (shortlisted, applied, ...) is his call, not the twin's.
        ~Posting.pipeline.has(Pipeline.stage != "new"),
    )
    if scope != "company":
        stmt = stmt.where(Posting.normalized_title == posting.normalized_title)
    swept = session.scalars(stmt.order_by(Posting.id)).all()
    for twin in swept:
        twin.status = "dismissed"
        twin.dismiss_reason = reason
        twin.prefilter_result = {**(twin.prefilter_result or {}), "twin_of": posting.id}
    return swept
