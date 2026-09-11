from __future__ import annotations

import logging
import threading

from jobfinder.config import load_profile
from jobfinder.db.models import Run, utcnow
from jobfinder.db.session import session_scope
from jobfinder.settings import get_settings

log = logging.getLogger(__name__)

# One scan at a time per process: the 05:00 cron, the hourly catch-up and the dashboard's
# "Scan now" all go through run_scan_job; a second caller is turned away, not queued.
SCAN_LOCK = threading.Lock()


def run_scan_job(llm=None, online=None) -> bool:  # noqa: ANN001
    """Run one full scan. Returns False when skipped (offline, or a scan is already running).

    Offline (no hostname resolves) the run is recorded as `error` / `offline` and nothing is
    fetched or scored: 2026-09-09 an offline scan spent two hours failing LLM calls and
    benched every source with an hour's cooldown.
    """
    from jobfinder.discovery.netcheck import is_online

    if not SCAN_LOCK.acquire(blocking=False):
        log.warning("scan skipped: another scan is running")
        return False
    try:
        if not (online or is_online)():
            log.warning("scan skipped: offline (no hostname resolves)")
            with session_scope() as session:
                session.add(Run(
                    kind="scan", status="error", finished_at=utcnow().replace(tzinfo=None),
                    stats={"error": "offline"},
                ))
            return False
        _run_scan(llm)
        return True
    finally:
        SCAN_LOCK.release()


def _run_scan(llm=None) -> None:  # noqa: ANN001
    import jobfinder.discovery.scan as scan_mod
    import jobfinder.llm as llm_mod
    from jobfinder.contacts.waterfall import run_for_new_matches
    from jobfinder.scoring.scorer import score_new_postings

    settings, profile = get_settings(), load_profile()
    llm = llm or llm_mod.get_llm(settings)
    contacts = None
    if profile.contacts.auto_run_on_match:
        def contacts(s):  # noqa: ANN001, ANN202
            return run_for_new_matches(s, llm=llm, settings=settings, profile=profile)
    elif profile.contacts.run_on_shortlist:
        # Catch-up for shortlisted matches whose Shortlist-time run never happened (server
        # restarted mid-run, thread failed): same waterfall, only for what JF picked.
        def contacts(s):  # noqa: ANN001, ANN202
            return run_for_new_matches(
                s, llm=llm, settings=settings, profile=profile, stage="shortlisted"
            )
    try:
        with session_scope() as session:
            scan_mod.run_scan(
                session, profile=profile, settings=settings, no_llm=False,
                scorer=lambda s: score_new_postings(s, llm=llm, profile=profile),
                contacts=contacts,
            )
    except Exception:  # noqa: BLE001
        log.exception("background scan failed")


def start_scan_thread(llm=None) -> threading.Thread:  # noqa: ANN001
    t = threading.Thread(target=run_scan_job, args=(llm,), name="jobfinder-scan", daemon=True)
    t.start()
    return t


# Postings whose contact run is in flight from a Shortlist click, so a second click (or the
# nightly catch-up racing the thread) does not start a duplicate run.
CONTACTS_IN_FLIGHT: set[int] = set()


def run_contacts_job(posting_id: int, llm=None) -> None:  # noqa: ANN001
    from jobfinder.contacts.waterfall import run_for_posting
    from jobfinder.llm import get_llm

    settings, profile = get_settings(), load_profile()
    llm = llm or get_llm(settings)
    try:
        with session_scope() as session:
            run_for_posting(session, posting_id, llm=llm, settings=settings, profile=profile)
    except Exception:  # noqa: BLE001
        log.exception("background contact run failed for posting %s", posting_id)
    finally:
        CONTACTS_IN_FLIGHT.discard(posting_id)


def start_contacts_thread(posting_id: int, llm=None) -> threading.Thread:  # noqa: ANN001
    t = threading.Thread(
        target=run_contacts_job, args=(posting_id, llm), name=f"jobfinder-contacts-{posting_id}",
        daemon=True,
    )
    t.start()
    return t
