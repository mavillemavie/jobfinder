from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from jobfinder.app.deps import db_session, templates
from jobfinder.db.models import Run

router = APIRouter(prefix="/runs")


@router.get("")
def runs(request: Request, session: Session = Depends(db_session)):  # noqa: ANN201
    rows = session.scalars(select(Run).order_by(Run.started_at.desc()).limit(50)).all()
    return templates.TemplateResponse(request, "runs.html", {"active": "runs", "rows": rows})
