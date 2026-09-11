from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from jobfinder.app.deps import current_profile, db_session, templates
from jobfinder.app.digest import build_digest, last_successful_digest, render_digest
from jobfinder.config import Profile
from jobfinder.db.models import utcnow

router = APIRouter(prefix="/brief")


@router.get("")
def brief_preview(
    request: Request,
    days: int | None = Query(None, ge=1, le=60),
    session: Session = Depends(db_session),
    profile: Profile = Depends(current_profile),
):  # noqa: ANN201
    """The next brief, rendered exactly as the email would be but with every match listed.
    Read-only: nothing is sent and no run is recorded, so the 07:00 brief's window (since the
    last *sent* brief) is untouched. `days` widens the window for a look further back."""
    since = (utcnow().replace(tzinfo=None) - timedelta(days=days)) if days else None
    data = build_digest(session, profile=profile, since=since, limit=None)
    subject, html, _text = render_digest(data)
    return templates.TemplateResponse(request, "brief.html", {
        "active": "brief", "subject": subject, "html": html, "d": data, "days": days,
        "last_sent": last_successful_digest(session),
    })
