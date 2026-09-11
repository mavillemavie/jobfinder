from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobfinder.db.models import Run, utcnow

log = logging.getLogger(__name__)


def close_stale_runs(session: Session, *, hours: int = 12) -> int:
    """Mark `running` runs older than `hours` as `error`.

    A run stays `running` when the process died mid-run or its final status write failed
    (2026-09-09: the digest's status update hit a stale SQLite snapshot). Called at service
    start, before the scheduler, so the catch-up job never waits on a ghost.
    """
    cutoff = utcnow().replace(tzinfo=None) - timedelta(hours=hours)
    stale = list(session.scalars(
        select(Run).where(Run.status == "running", Run.started_at < cutoff)
    ))
    for run in stale:
        run.status = "error"
        run.finished_at = utcnow().replace(tzinfo=None)
        run.stats = {**(run.stats or {}), "error": "stale: process restarted"}
        log.warning("closing stale %s run %s started %s", run.kind, run.id, run.started_at)
    if stale:
        session.commit()
    return len(stale)
