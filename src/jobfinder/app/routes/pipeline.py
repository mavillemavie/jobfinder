from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from jobfinder.app.deps import db_session, templates
from jobfinder.app.routes.inbox import _log_stage
from jobfinder.app.routes.jobs import CLOSE_REASONS, STAGES
from jobfinder.db.models import Pipeline, Posting

router = APIRouter(prefix="/pipeline")


@router.get("")
def board(request: Request, session: Session = Depends(db_session)):  # noqa: ANN201
    rows = session.scalars(
        select(Pipeline).options(
            selectinload(Pipeline.posting).selectinload(Posting.company),
            selectinload(Pipeline.posting).selectinload(Posting.contacts),
        )
    ).all()
    columns = {s: [pl for pl in rows if pl.stage == s] for s in STAGES}
    return templates.TemplateResponse(request, "pipeline.html", {
        "active": "pipeline", "columns": columns, "stages": STAGES, "close_reasons": CLOSE_REASONS,
    })


@router.post("/{posting_id}/move")
def move(
    posting_id: int,
    stage: str = Form(...),
    close_reason: str | None = Form(None),
    session: Session = Depends(db_session),
):  # noqa: ANN201
    p = session.get(Posting, posting_id)
    if p is None or stage not in STAGES:
        raise HTTPException(400, "bad move")
    reason = close_reason if stage == "closed" and close_reason in CLOSE_REASONS else None
    _log_stage(session, p, stage, reason)
    session.commit()
    return RedirectResponse("/pipeline", status_code=303)
