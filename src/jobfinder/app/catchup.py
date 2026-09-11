"""Hourly catch-up: run today's scan and/or digest if their scheduled slot was missed.

2026-09-09 the PC had no network from a 22:09 reboot until 23:46 the next night; the 05:00
scan and the 07:00 digest both failed and JF got no brief. This job fires every hour at :30
and repairs the day once the network is back: a scan if no `ok` scan started at/after today's
scan slot, a digest if no `ok` digest finished at/after today's digest slot. It never runs
before the slot, never after 22:00, never doubles a running scan, and a manual dashboard
scan never triggers a digest (slot rule only).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jobfinder.app import background
from jobfinder.app.background import run_scan_job
from jobfinder.app.digest import last_successful_digest, send_digest
from jobfinder.config import Profile, load_profile
from jobfinder.db.models import Run
from jobfinder.db.session import session_scope
from jobfinder.settings import get_settings

log = logging.getLogger(__name__)

SLOT_GRACE = timedelta(minutes=15)  # let the scheduled job (and the digest's 5-min retry) finish
EARLY_TOLERANCE = timedelta(seconds=60)  # a cron firing a hair before the slot still counts
LAST_HOUR = 22  # no scans or briefs late at night


@dataclass(frozen=True)
class CatchupPlan:
    scan: bool
    digest: bool
    reason: str


def _slot(now_local: datetime, hhmm: str, tz: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return now_local.astimezone(ZoneInfo(tz)).replace(hour=h, minute=m, second=0, microsecond=0)


def _utc_naive(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None)


def plan_catchup(
    *,
    now_local: datetime,
    profile: Profile,
    last_scan_ok: datetime | None,
    last_digest_ok: datetime | None,
    scan_busy: bool,
) -> CatchupPlan:
    """Pure decision. `last_scan_ok` = started_at of the latest `ok` scan, `last_digest_ok` =
    finished_at of the latest `ok` digest, both naive UTC as stored."""
    scan_slot = _slot(now_local, profile.scan.run_at, profile.scan.timezone)
    if now_local < scan_slot + SLOT_GRACE:
        return CatchupPlan(False, False, "before today's scan slot")
    if now_local.astimezone(ZoneInfo(profile.scan.timezone)).hour >= LAST_HOUR:
        return CatchupPlan(False, False, f"after {LAST_HOUR}:00")

    reasons = []
    scan_missing = last_scan_ok is None or last_scan_ok < _utc_naive(scan_slot) - EARLY_TOLERANCE
    scan = scan_missing and not scan_busy
    if scan_missing and scan_busy:
        reasons.append("scan missing but one is running")
    elif scan:
        reasons.append("today's scan missing")

    digest = False
    if profile.digest.enabled:
        digest_slot = _slot(now_local, profile.digest.send_at, profile.digest.timezone)
        if now_local >= digest_slot + SLOT_GRACE:
            digest = (
                last_digest_ok is None
                or last_digest_ok < _utc_naive(digest_slot) - EARLY_TOLERANCE
            )
            if digest:
                reasons.append("today's digest missing")
    return CatchupPlan(scan, digest, "; ".join(reasons) or "nothing to do")


def _last_ok(session: Session) -> tuple[datetime | None, datetime | None]:
    last_scan = session.scalar(
        select(func.max(Run.started_at)).where(Run.kind == "scan", Run.status == "ok")
    )
    return last_scan, last_successful_digest(session)


def scheduled_catchup(
    online: Callable[[], bool] | None = None, *, now: datetime | None = None
) -> CatchupPlan:
    from jobfinder.discovery.netcheck import is_online

    profile = load_profile()
    now_local = now or datetime.now(ZoneInfo(profile.scan.timezone))
    with session_scope() as session:
        last_scan_ok, last_digest_ok = _last_ok(session)
    plan = plan_catchup(
        now_local=now_local, profile=profile, last_scan_ok=last_scan_ok,
        last_digest_ok=last_digest_ok, scan_busy=background.SCAN_LOCK.locked(),
    )
    if not (plan.scan or plan.digest):
        log.debug("catch-up: %s", plan.reason)
        return plan
    if not (online or is_online)():
        log.warning("catch-up skipped (offline): %s", plan.reason)
        return CatchupPlan(False, False, "offline")
    log.warning("catch-up: %s", plan.reason)
    if plan.scan:
        ran = run_scan_job()
        log.warning("catch-up scan %s", "done" if ran else "skipped")
    if plan.digest:
        with session_scope() as session:
            run = send_digest(
                session, settings=get_settings(), profile=profile, retry_delay_s=0
            )
            log.warning("catch-up digest %s", run.status)
    return plan
