from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from jobfinder.config import Profile, load_profile
from jobfinder.db.session import session_scope
from jobfinder.settings import get_settings

log = logging.getLogger(__name__)


def scheduled_scan() -> None:
    from jobfinder.app.background import run_scan_job

    log.info("scheduled scan starting")
    run_scan_job()


def scheduled_digest() -> None:
    from jobfinder.app.digest import send_digest

    try:
        with session_scope() as session:
            run = send_digest(session, settings=get_settings(), profile=load_profile())
            log.info("scheduled digest %s", run.status)
    except Exception:  # noqa: BLE001 — a failed digest must never kill the scheduler
        log.exception("scheduled digest failed")


def scheduled_catchup_job() -> None:
    from jobfinder.app.catchup import scheduled_catchup

    try:
        scheduled_catchup()
    except Exception:  # noqa: BLE001 — a failed catch-up must never kill the scheduler
        log.exception("scheduled catch-up failed")


def _hm(value: str) -> tuple[int, int]:
    h, m = value.split(":")
    return int(h), int(m)


def build_scheduler(profile: Profile) -> BackgroundScheduler:
    sched = BackgroundScheduler(
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600}
    )
    h, m = _hm(profile.scan.run_at)
    sched.add_job(
        scheduled_scan, CronTrigger(hour=h, minute=m, timezone=profile.scan.timezone),
        id="scan", replace_existing=True,
    )
    if profile.digest.enabled:
        h, m = _hm(profile.digest.send_at)
        sched.add_job(
            scheduled_digest, CronTrigger(hour=h, minute=m, timezone=profile.digest.timezone),
            id="digest", replace_existing=True,
        )
    # Hourly repair of a missed scan/digest slot (offline morning); no-op on a normal day.
    sched.add_job(
        scheduled_catchup_job, CronTrigger(minute=30, timezone=profile.scan.timezone),
        id="catchup", replace_existing=True,
    )
    return sched
