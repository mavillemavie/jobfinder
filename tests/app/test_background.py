from sqlalchemy import select

from jobfinder.app import background
from jobfinder.app.background import run_scan_job
from jobfinder.db.models import Run


def test_offline_scan_records_an_error_run_and_never_scans(home, db_session, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("run_scan must not be called offline")

    monkeypatch.setattr("jobfinder.discovery.scan.run_scan", boom)
    assert run_scan_job(online=lambda: False) is False
    runs = db_session.scalars(select(Run)).all()
    assert len(runs) == 1
    run = runs[0]
    assert run.kind == "scan" and run.status == "error" and run.finished_at is not None
    assert run.stats["error"] == "offline"


def test_online_scan_runs_and_holds_the_lock(home, db_session, monkeypatch):
    calls = []

    def fake_run_scan(session, **kw):
        calls.append(kw)
        assert background.SCAN_LOCK.locked()
        return Run(kind="scan", status="ok")

    monkeypatch.setattr("jobfinder.discovery.scan.run_scan", fake_run_scan)
    monkeypatch.setattr("jobfinder.llm.get_llm", lambda settings: object())
    assert run_scan_job(online=lambda: True) is True
    assert len(calls) == 1 and not background.SCAN_LOCK.locked()


def test_second_scan_is_skipped_while_one_runs(home, db_session, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not start a second scan")

    monkeypatch.setattr("jobfinder.discovery.scan.run_scan", boom)
    background.SCAN_LOCK.acquire()
    try:
        assert run_scan_job(online=lambda: True) is False
    finally:
        background.SCAN_LOCK.release()
    assert db_session.scalars(select(Run)).all() == []
