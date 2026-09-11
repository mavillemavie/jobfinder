from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from jobfinder.app import catchup
from jobfinder.app.catchup import plan_catchup, scheduled_catchup
from jobfinder.config import load_profile
from jobfinder.db.models import Run

TZ = ZoneInfo("America/Montreal")


def _local(hhmm: str, day: str = "2026-09-10") -> datetime:
    return datetime.fromisoformat(f"{day}T{hhmm}").replace(tzinfo=TZ)


def _utc(hhmm: str, day: str = "2026-09-10") -> datetime:
    """A run timestamp as stored: naive UTC, from a Montreal wall-clock time."""
    return _local(hhmm, day).astimezone(UTC).replace(tzinfo=None)


def _plan(now: str, *, scan=None, digest=None, busy=False, profile=None):
    profile = profile or load_profile()  # fixture profile: scan 06:00, digest 07:00, Montreal
    return plan_catchup(
        now_local=_local(now), profile=profile, last_scan_ok=scan, last_digest_ok=digest,
        scan_busy=busy,
    )


def test_nothing_before_the_scan_slot_plus_grace(home) -> None:
    assert _plan("05:30").scan is False and _plan("05:30").digest is False
    assert _plan("06:10").scan is False  # 06:00 job may still be running / just started
    assert "before" in _plan("06:10").reason


def test_normal_day_needs_nothing(home) -> None:
    p = _plan("07:30", scan=_utc("06:00"), digest=_utc("07:00"))
    assert (p.scan, p.digest) == (False, False)


def test_scan_ok_but_digest_missing_sends_digest_only(home) -> None:
    p = _plan("07:30", scan=_utc("06:00"), digest=_utc("07:00", "2026-09-09"))
    assert (p.scan, p.digest) == (False, True)


def test_digest_slot_gets_its_own_grace(home) -> None:
    # 07:00 job retries once at 07:05; do not race it.
    p = _plan("07:10", scan=_utc("06:00"), digest=_utc("07:00", "2026-09-09"))
    assert p.digest is False


def test_scan_missing_but_digest_ok_scans_only(home) -> None:
    p = _plan("07:30", scan=_utc("06:00", "2026-09-09"), digest=_utc("07:00"))
    assert (p.scan, p.digest) == (True, False)


def test_both_missing_after_an_outage(home) -> None:
    p = _plan("09:30", scan=None, digest=None)
    assert (p.scan, p.digest) == (True, True)


def test_running_scan_is_not_doubled(home) -> None:
    p = _plan("06:30", scan=None, busy=True)
    assert p.scan is False and "running" in p.reason


def test_nothing_after_22h(home) -> None:
    p = _plan("22:30", scan=None, digest=None)
    assert (p.scan, p.digest) == (False, False)


def test_disabled_digest_is_never_sent(home) -> None:
    profile = load_profile()
    profile.digest.enabled = False
    p = _plan("09:30", scan=None, digest=None, profile=profile)
    assert (p.scan, p.digest) == (True, False)


def test_a_scan_started_a_minute_early_still_counts(home) -> None:
    p = _plan("07:30", scan=_utc("05:59:30"), digest=_utc("07:00"))
    assert p.scan is False


def _never(*a, **k):
    raise AssertionError("must not be called")


def _seed_runs(db_session, *, scan_ok: datetime | None, digest_ok: datetime | None) -> None:
    if scan_ok:
        db_session.add(Run(kind="scan", status="ok", started_at=scan_ok,
                           finished_at=scan_ok + timedelta(minutes=30)))
    if digest_ok:
        db_session.add(Run(kind="digest", status="ok", started_at=digest_ok,
                           finished_at=digest_ok + timedelta(seconds=2)))
    db_session.add(Run(kind="scan", status="error", started_at=_utc("06:00"),
                       finished_at=_utc("06:00"), stats={"error": "offline"}))
    db_session.commit()


def test_scheduled_catchup_runs_scan_then_digest(home, db_session, monkeypatch) -> None:
    _seed_runs(db_session, scan_ok=_utc("06:00", "2026-09-09"),
               digest_ok=_utc("07:00", "2026-09-09"))
    calls: list[str] = []
    monkeypatch.setattr(catchup, "run_scan_job", lambda: calls.append("scan") or True)

    def fake_send(session, *, settings, profile, retry_delay_s):
        calls.append(f"digest:{retry_delay_s}")
        return Run(kind="digest", status="ok")

    monkeypatch.setattr(catchup, "send_digest", fake_send)
    plan = scheduled_catchup(online=lambda: True, now=_local("09:30"))
    assert (plan.scan, plan.digest) == (True, True)
    assert calls == ["scan", "digest:0"]


def test_scheduled_catchup_does_nothing_offline_and_writes_no_run(
    home, db_session, monkeypatch
) -> None:
    _seed_runs(db_session, scan_ok=None, digest_ok=None)
    monkeypatch.setattr(catchup, "run_scan_job", _never)
    monkeypatch.setattr(catchup, "send_digest", _never)
    before = len(db_session.scalars(select(Run)).all())
    plan = scheduled_catchup(online=lambda: False, now=_local("09:30"))
    assert plan.reason == "offline" and (plan.scan, plan.digest) == (False, False)
    db_session.expire_all()
    assert len(db_session.scalars(select(Run)).all()) == before


def test_scheduled_catchup_is_quiet_on_a_normal_day(home, db_session, monkeypatch) -> None:
    _seed_runs(db_session, scan_ok=_utc("06:00"), digest_ok=_utc("07:00"))
    monkeypatch.setattr(catchup, "run_scan_job", _never)
    monkeypatch.setattr(catchup, "send_digest", _never)
    online_checks = []
    plan = scheduled_catchup(online=lambda: online_checks.append(1) or True, now=_local("09:30"))
    assert (plan.scan, plan.digest) == (False, False)
    assert online_checks == []  # nothing to do → no DNS probe either
