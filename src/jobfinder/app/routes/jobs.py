from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from jobfinder.app.deps import (
    current_profile,
    current_settings,
    db_session,
    get_llm_dep,
    templates,
)
from jobfinder.app.routes.inbox import _log_stage, close_twins
from jobfinder.config import Profile
from jobfinder.db.models import Activity, Contact, Document, Posting, utcnow
from jobfinder.outreach.gmail_link import gmail_compose_url
from jobfinder.settings import Settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs")
STAGES = [
    "new", "shortlisted", "docs_ready", "applied", "contacted", "interviewing", "offer", "closed",
]
CLOSE_REASONS = ["rejected", "withdrawn", "hired", "stale"]
ACTIVITY_KINDS = ["call", "email", "note"]
DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _get(session: Session, posting_id: int) -> Posting:
    p = session.get(Posting, posting_id)
    if p is None:
        raise HTTPException(404, "no such posting")
    return p


def _ctx(p: Posting) -> dict:
    return {
        "p": p, "score": p.latest_score, "stages": STAGES, "close_reasons": CLOSE_REASONS,
        "activity_kinds": ACTIVITY_KINDS,
        "contacts": sorted(p.contacts, key=lambda c: -c.confidence),
        "documents": sorted(p.documents, key=lambda d: (d.kind, d.format)),
        "activities": sorted(p.activities, key=lambda a: a.occurred_at, reverse=True),
        "drafts": p.drafts, "gmail": gmail_compose_url,
        "email_contact": next(
            (c for c in p.contacts if any(d.contact_id == c.id for d in p.drafts)), None
        ),
    }


@router.get("/{posting_id}")
def job_page(
    posting_id: int,
    request: Request,
    session: Session = Depends(db_session),
    profile: Profile = Depends(current_profile),
):  # noqa: ANN201
    p = _get(session, posting_id)
    return templates.TemplateResponse(
        request, "job.html", {"active": "inbox", **_ctx(p), "profile": profile}
    )


@router.post("/{posting_id}/tailor")
def tailor(
    posting_id: int,
    request: Request,
    session: Session = Depends(db_session),
    profile: Profile = Depends(current_profile),
):  # noqa: ANN201
    from jobfinder.tailoring.tailor import tailor_posting

    tailor_posting(session, posting_id, llm=get_llm_dep(request), profile=profile)
    session.commit()
    return templates.TemplateResponse(request, "_documents.html", _ctx(_get(session, posting_id)))


@router.post("/{posting_id}/contacts")
def find_contacts(
    posting_id: int,
    request: Request,
    session: Session = Depends(db_session),
    profile: Profile = Depends(current_profile),
    settings: Settings = Depends(current_settings),
):  # noqa: ANN201
    try:
        from jobfinder.contacts.waterfall import run_for_posting
    except ImportError as exc:
        raise HTTPException(501, "contact discovery is not built yet (Plan 3)") from exc
    error = None
    try:
        run_for_posting(
            session, posting_id, llm=get_llm_dep(request), settings=settings, profile=profile
        )
        session.commit()
    except Exception as exc:  # noqa: BLE001 — show the failure in the panel, keep the page alive
        log.exception("contact discovery failed for posting %s", posting_id)
        session.rollback()
        error = f"contact discovery failed: {exc!r}"[:300]
    ctx = _ctx(_get(session, posting_id))
    return templates.TemplateResponse(request, "_contacts.html", {**ctx, "error": error})


@router.post("/{posting_id}/contacts/{contact_id}/delete")
def delete_contact(
    posting_id: int, contact_id: int, request: Request, session: Session = Depends(db_session)
):  # noqa: ANN201
    c = session.get(Contact, contact_id)
    if c is not None and c.posting_id == posting_id:
        session.delete(c)
        session.commit()
    return templates.TemplateResponse(request, "_contacts.html", _ctx(_get(session, posting_id)))


@router.post("/{posting_id}/stage")
def set_stage(
    posting_id: int,
    stage: str = Form(...),
    close_reason: str | None = Form(None),
    session: Session = Depends(db_session),
):  # noqa: ANN201
    p = _get(session, posting_id)
    if stage not in STAGES:
        raise HTTPException(400, "bad stage")
    reason = close_reason if stage == "closed" and close_reason in CLOSE_REASONS else None
    _log_stage(session, p, stage, reason)
    if reason == "rejected":
        # The employer said no: the same title elsewhere in that company is not worth a
        # second application, so its live twins are dismissed (and blocked at ingest).
        close_twins(session, p, "rejected", "title")
    if stage == "applied" and p.pipeline.applied_at is None:
        p.pipeline.applied_at = utcnow().replace(tzinfo=None)
    session.commit()
    return RedirectResponse(f"/jobs/{posting_id}", status_code=303)


@router.post("/{posting_id}/notes")
def set_notes(
    posting_id: int, notes: str = Form(""), session: Session = Depends(db_session)
):  # noqa: ANN201
    p = _get(session, posting_id)
    if p.pipeline is None:
        _log_stage(session, p, "new")
    p.pipeline.notes = notes
    session.commit()
    return {"ok": True}


@router.post("/{posting_id}/activities")
def add_activity(
    posting_id: int,
    request: Request,
    kind: str = Form("note"),
    outcome: str = Form(""),
    body: str = Form(""),
    session: Session = Depends(db_session),
):  # noqa: ANN201
    p = _get(session, posting_id)
    session.add(Activity(
        posting_id=p.id, kind=kind if kind in ACTIVITY_KINDS else "note",
        outcome=outcome or None, body=body,
    ))
    if kind in ("call", "email") and p.pipeline and p.pipeline.stage == "applied":
        _log_stage(session, p, "contacted")
    session.commit()
    return templates.TemplateResponse(request, "_activities.html", _ctx(_get(session, posting_id)))


@router.get("/{posting_id}/files/{doc_id}")
def file(posting_id: int, doc_id: int, session: Session = Depends(db_session)):  # noqa: ANN201
    d = session.get(Document, doc_id)
    if d is None or d.posting_id != posting_id or not Path(d.path).exists():
        raise HTTPException(404, "no such document")
    media = DOCX_MEDIA if d.format == "docx" else "application/pdf"
    return FileResponse(d.path, media_type=media, filename=Path(d.path).name)


@router.post("/{posting_id}/drafts")
def make_drafts(
    posting_id: int,
    request: Request,
    contact_id: int | None = Form(None),
    session: Session = Depends(db_session),
    profile: Profile = Depends(current_profile),
    settings: Settings = Depends(current_settings),
):  # noqa: ANN201
    from jobfinder.outreach.drafts import draft_for_posting, load_voice_notes

    draft_for_posting(
        session, posting_id, llm=get_llm_dep(request), profile=profile, contact_id=contact_id,
        voice_notes=load_voice_notes(settings),
    )
    return templates.TemplateResponse(request, "_drafts.html", _ctx(_get(session, posting_id)))


@router.post("/{posting_id}/drafts/{draft_id}")
def edit_draft(
    posting_id: int,
    draft_id: int,
    request: Request,
    subject: str | None = Form(None),
    body: str = Form(""),
    session: Session = Depends(db_session),
):  # noqa: ANN201
    from jobfinder.db.models import Draft

    d = session.get(Draft, draft_id)
    if d is None or d.posting_id != posting_id:
        raise HTTPException(404, "no such draft")
    d.subject, d.body = (subject if d.kind == "email" else None), body
    session.commit()
    return templates.TemplateResponse(request, "_drafts.html", _ctx(_get(session, posting_id)))
