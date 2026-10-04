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


def test_run_score_job_scores_unscored_posting_once(home) -> None:
    from sqlalchemy.orm import Session

    from jobfinder import paths
    from jobfinder.db.models import Company, Posting
    from jobfinder.db.session import get_engine
    from jobfinder.llm.fake import FakeLLM

    fixture = paths.repo_root() / "tests/fixtures/master/resume.yaml"
    (paths.master_dir() / "resume.yaml").write_text(
        fixture.read_text(encoding="utf-8"), encoding="utf-8"
    )
    with Session(get_engine()) as s:
        p = Posting(
            company=Company(name="Acme", normalized_name="acme"), title="Data Analyst",
            normalized_title="data analyst", apply_url="u", dedupe_key="k", content_hash="h",
            description_text="Power BI", prefilter_result={"manual": True},
        )
        s.add(p)
        s.commit()
        pid = p.id
    fake = FakeLLM({"score_posting": {
        "fit_score": 88, "reasons": ["bi"], "missing_requirements": [],
        "seniority_match": "match",
        "eligibility": {
            "remote_from_canada": "unclear", "uk_right_to_work_required": "no",
            "sponsorship_mentioned": "no",
        },
        "language": "en", "red_flags": [], "one_line_summary": "good",
    }})
    background.SCORING_IN_FLIGHT.add(pid)
    background.run_score_job(pid, fake)
    background.run_score_job(pid, fake)  # already scored → no second call
    assert len(fake.calls) == 1 and pid not in background.SCORING_IN_FLIGHT
    with Session(get_engine()) as s:
        assert s.get(Posting, pid).latest_score.fit_score == 88


def test_run_score_job_swallows_errors(home) -> None:
    background.SCORING_IN_FLIGHT.add(999)
    background.run_score_job(999, object())  # no such posting, bogus llm: must not raise
    assert 999 not in background.SCORING_IN_FLIGHT


def test_claim_scoring_is_once_per_posting() -> None:
    background.SCORING_IN_FLIGHT.clear()
    assert background.claim_scoring(7) is True
    assert background.claim_scoring(7) is False  # double submit: second save does not score
    background.SCORING_IN_FLIGHT.discard(7)
    assert background.claim_scoring(7) is True
    background.SCORING_IN_FLIGHT.clear()
