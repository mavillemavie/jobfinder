from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from jobfinder.app.background import CONTACTS_IN_FLIGHT, start_contacts_thread
from jobfinder.app.deps import current_profile, db_session, get_llm_dep, templates
from jobfinder.config import Profile
from jobfinder.db.models import Activity, ContactRun, Pipeline, Posting
from jobfinder.discovery.twins import scope_for, sweep_twins

router = APIRouter()
DISMISS_REASONS = [
    "wrong_title", "wrong_location", "too_senior", "too_junior", "bad_company", "other",
]


def inbox_query(
    session: Session, status: str, min_score: int | None, location: str | None, q: str | None
) -> list[Posting]:
    stmt = select(Posting).options(
        selectinload(Posting.scores), selectinload(Posting.company), selectinload(Posting.pipeline)
    )
    if status != "all":
        stmt = stmt.where(Posting.status == status)
    if status == "match":
        # A match whose pipeline JF closed (rejected, withdrawn, hired, stale) is history,
        # not an actionable row; it stays reachable under "all" and on the pipeline board.
        stmt = stmt.where(~Posting.pipeline.has(Pipeline.stage == "closed"))
    if q:
        stmt = stmt.where(Posting.title.ilike(f"%{q}%"))
    rows = list(session.scalars(stmt))
    if location:
        rows = [p for p in rows if (p.prefilter_result or {}).get("location_key") == location]
    if min_score is not None:
        rows = [
            p for p in rows
            if p.latest_score is not None and p.latest_score.fit_score >= min_score
        ]
    rows.sort(
        key=lambda p: (
            -(p.latest_score.fit_score if p.latest_score else -1),
            -p.first_seen_at.timestamp(),
        )
    )
    return rows


def _log_stage(session: Session, posting: Posting, stage: str, reason: str | None = None) -> None:
    if posting.pipeline is None:
        posting.pipeline = Pipeline(stage="new")
    old = posting.pipeline.stage
    posting.pipeline.stage = stage
    if reason:
        posting.pipeline.close_reason = reason
    body = f"{old} → {stage}" + (f" ({reason})" if reason else "")
    session.add(Activity(posting_id=posting.id, kind="stage_change", outcome=stage, body=body))


def shortlist_posting(session: Session, posting: Posting) -> None:
    _log_stage(session, posting, "shortlisted")


def dismiss_posting(session: Session, posting: Posting, reason: str) -> int:
    """Dismiss the row and, per the reason's scope, its live twins (same company and title,
    or the whole company for bad_company). Returns how many twins were swept."""
    posting.status = "dismissed"
    posting.dismiss_reason = reason if reason in DISMISS_REASONS else "other"
    _log_stage(session, posting, "closed", "withdrawn")
    return close_twins(session, posting, posting.dismiss_reason, scope_for(posting.dismiss_reason))


def close_twins(session: Session, posting: Posting, reason: str, scope: str) -> int:
    swept = sweep_twins(session, posting, reason, scope)
    for twin in swept:
        _log_stage(session, twin, "closed", "withdrawn")
    return len(swept)


def _row(request: Request, posting: Posting, note: str = ""):  # noqa: ANN202
    return templates.TemplateResponse(
        request, "_posting_row.html", {"p": posting, "note": note, "reasons": DISMISS_REASONS}
    )


@router.get("/")
def inbox(
    request: Request,
    status: str = "match",
    min_score: int | None = None,
    location: str | None = None,
    q: str | None = None,
    session: Session = Depends(db_session),
    profile: Profile = Depends(current_profile),
):  # noqa: ANN201
    rows = inbox_query(session, status, min_score, location, q)
    return templates.TemplateResponse(request, "inbox.html", {
        "active": "inbox", "rows": rows, "status": status, "min_score": min_score,
        "location": location, "q": q,
        "locations": [loc.key for loc in profile.enabled_locations()], "reasons": DISMISS_REASONS,
    })


@router.post("/postings/{posting_id}/shortlist")
def shortlist(
    posting_id: int,
    request: Request,
    session: Session = Depends(db_session),
    profile: Profile = Depends(current_profile),
):  # noqa: ANN201
    p = session.get(Posting, posting_id)
    shortlist_posting(session, p)
    session.commit()
    note = "shortlisted"
    if profile.contacts.run_on_shortlist and posting_id not in CONTACTS_IN_FLIGHT:
        has_run = session.scalar(select(ContactRun.id).where(ContactRun.posting_id == posting_id))
        if has_run is None:
            # Contacts cost credits: run them for what JF picks, in the background so the
            # click returns at once. The nightly catch-up covers a thread that never finished.
            CONTACTS_IN_FLIGHT.add(posting_id)
            start_contacts_thread(posting_id, get_llm_dep(request))
            note = "shortlisted · finding contact…"
    return _row(request, p, note)


@router.post("/postings/{posting_id}/dismiss")
def dismiss(
    posting_id: int,
    request: Request,
    reason: str = Form("other"),
    session: Session = Depends(db_session),
):  # noqa: ANN201
    p = session.get(Posting, posting_id)
    n = dismiss_posting(session, p, reason)
    session.commit()
    note = "dismissed" if not n else f"dismissed · {n} twin{'s' if n != 1 else ''} too"
    return _row(request, p, note)


@router.post("/postings/{posting_id}/rescore")
def rescore(
    posting_id: int,
    request: Request,
    session: Session = Depends(db_session),
    profile: Profile = Depends(current_profile),
):  # noqa: ANN201
    from jobfinder.scoring.scorer import score_posting
    from jobfinder.tailoring.master_schema import load_master

    llm = get_llm_dep(request)
    if hasattr(llm, "bypass_next"):
        llm.bypass_next()
    p = session.get(Posting, posting_id)
    score_posting(session, p, master=load_master(), llm=llm, profile=profile)
    session.commit()
    return _row(request, p, "re-scored")
