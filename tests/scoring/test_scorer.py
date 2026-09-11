from pathlib import Path

from sqlalchemy import select

from jobfinder import paths
from jobfinder.config import load_profile
from jobfinder.db.models import Company, Posting, Score
from jobfinder.llm.base import LLMOutputError
from jobfinder.llm.fake import FakeLLM
from jobfinder.scoring.scorer import score_new_postings, select_candidates

FIXTURE = Path(__file__).parent.parent / "fixtures" / "master" / "resume.yaml"


def _good(score: int, lang: str = "en") -> dict:
    return {
        "fit_score": score, "reasons": ["r1"], "missing_requirements": ["dbt"],
        "seniority_match": "match",
        "eligibility": {
            "remote_from_canada": "yes", "uk_right_to_work_required": "no",
            "sponsorship_mentioned": "no",
        },
        "language": lang, "red_flags": [], "one_line_summary": "fine",
    }


def _seed(db_session, n: int, **kw) -> list[Posting]:
    co = Company(name="Acme", normalized_name="acme")
    out = []
    for i in range(n):
        p = Posting(
            company=co, title=f"Data Analyst {i}", normalized_title="data analyst",
            apply_url=f"u{i}", dedupe_key=f"k{i}", content_hash=f"h{i}",
            description_text="SQL Power BI", status="new", description_complete=True,
            prefilter_result={"passed": True, "reason": "ok", "overlap": 5 - i, "stage": "pass2"},
            **kw,
        )
        db_session.add(p)
        out.append(p)
    db_session.commit()
    return out


def test_select_candidates_skips_snippet_only_postings(db_session) -> None:
    ps = _seed(db_session, 2)
    ps[1].description_complete = False
    ps[1].prefilter_result = {"passed": True, "reason": "ok", "stage": "pass1"}
    db_session.commit()
    assert [p.id for p in select_candidates(db_session, limit=10)] == [ps[0].id]


def test_select_candidates_orders_by_overlap_and_includes_rescore(db_session) -> None:
    ps = _seed(db_session, 3)
    ps[2].status, ps[2].prefilter_result = (
        "match", {"passed": True, "reason": "ok", "overlap": 9, "rescore": True}
    )
    db_session.commit()
    ids = [p.id for p in select_candidates(db_session, limit=10)]
    assert ids == [ps[2].id, ps[0].id, ps[1].id]
    assert [p.id for p in select_candidates(db_session, limit=1)] == [ps[2].id]


def test_score_new_postings_sets_status_pipeline_and_language(home, db_session) -> None:
    (paths.master_dir() / "resume.yaml").write_text(FIXTURE.read_text())
    ps = _seed(db_session, 2)
    llm = FakeLLM({"score_posting": [_good(85, "fr"), _good(40)]})
    stats = score_new_postings(db_session, llm=llm, profile=load_profile())
    assert stats == {"scored": 2, "matches": 1, "needs_review": 0, "skipped_no_master": 0}
    first, second = db_session.get(Posting, ps[0].id), db_session.get(Posting, ps[1].id)
    assert first.status == "match" and first.language == "fr" and first.pipeline.stage == "new"
    assert second.status == "scored" and second.pipeline is None
    assert db_session.scalar(select(Score).where(Score.posting_id == first.id)).fit_score == 85
    assert llm.calls[0]["tier"] == "fast" and "Data Analyst 0" in llm.calls[0]["user"]
    assert "Reporting Analyst — Acme Logistics" in llm.calls[0]["user"]
    assert "jordan@example.com" not in llm.calls[0]["user"]


def test_llm_failure_marks_needs_review_and_continues(home, db_session) -> None:
    (paths.master_dir() / "resume.yaml").write_text(FIXTURE.read_text())
    ps = _seed(db_session, 2)
    llm = FakeLLM({"score_posting": lambda user: (_ for _ in ()).throw(LLMOutputError("bad"))})
    stats = score_new_postings(db_session, llm=llm, profile=load_profile())
    assert stats["needs_review"] == 2 and stats["scored"] == 0
    p = db_session.get(Posting, ps[0].id)
    assert p.status == "new" and p.prefilter_result["needs_review"] == "llm_output"


def test_without_master_skips(home, db_session) -> None:
    _seed(db_session, 1)
    stats = score_new_postings(db_session, llm=FakeLLM(), profile=load_profile())
    assert stats["skipped_no_master"] == 1


def test_score_prompt_carries_candidate_eligibility(home, db_session) -> None:
    """The scorer must know which locations the candidate enabled and their UK status, or every UK
    posting is docked for right-to-work (day-1 brief: 58 UK scores, all < 70)."""
    (paths.master_dir() / "resume.yaml").write_text(FIXTURE.read_text())
    _seed(db_session, 1)
    profile = load_profile()
    profile.scoring.candidate_note = "UK work visa is pending; UK on-site roles are acceptable."
    llm = FakeLLM({"score_posting": [_good(85)]})
    score_new_postings(db_session, llm=llm, profile=profile)
    user = llm.calls[0]["user"]
    assert "CANDIDATE ELIGIBILITY" in user
    assert "uk (GB)" in user and "montreal (CA" in user
    assert "UK work visa is pending" in user
    assert user.index("CANDIDATE ELIGIBILITY") < user.index("POSTING")


def test_select_candidates_prefers_enabled_location_order(db_session) -> None:
    """Scoring is capped per run; a UK posting with the best overlap must not push a Canadian
    one out of the day's budget (Canada comes before uk in the enabled list)."""
    ps = _seed(db_session, 3)
    ps[0].prefilter_result = {**ps[0].prefilter_result, "location_key": "uk", "overlap": 9}
    ps[1].prefilter_result = {**ps[1].prefilter_result, "location_key": "montreal", "overlap": 2}
    ps[2].status = "match"
    ps[2].prefilter_result = {
        **ps[2].prefilter_result, "location_key": "uk", "overlap": 1, "rescore": True
    }
    db_session.commit()
    profile = load_profile()
    ids = [p.id for p in select_candidates(db_session, limit=10, profile=profile)]
    assert ids == [ps[1].id, ps[2].id, ps[0].id]
    # Without a profile the old order (rescore first, then overlap) still holds.
    assert [p.id for p in select_candidates(db_session, limit=10)] == [ps[2].id, ps[0].id, ps[1].id]


def _commit_elsewhere() -> None:
    """Another thread (the digest at 07:00) committing a Run row on its own connection."""
    from sqlalchemy.orm import Session

    from jobfinder.db.models import Run
    from jobfinder.db.session import get_engine

    other = Session(get_engine(), expire_on_commit=False)
    other.add(Run(kind="digest", status="ok"))
    other.commit()
    other.close()


def test_scoring_survives_a_commit_from_another_connection_during_the_llm_call(
    home, db_session
) -> None:
    """2026-09-09: the digest inserted its Run row while the scorer was inside a 3-minute
    `claude -p` call; the scorer's next write then failed with "database is locked"."""
    (paths.master_dir() / "resume.yaml").write_text(FIXTURE.read_text())
    _seed(db_session, 2)

    def slow_llm(user: str) -> dict:
        _commit_elsewhere()
        return _good(85)

    llm = FakeLLM({"score_posting": slow_llm})
    stats = score_new_postings(db_session, llm=llm, profile=load_profile())
    assert stats["scored"] == 2 and stats["matches"] == 2
    assert {p.status for p in db_session.scalars(select(Posting))} == {"match"}


def test_three_consecutive_llm_failures_abort_the_run(home, db_session) -> None:
    """Offline, every `claude -p` call takes minutes to fail: 2026-09-09 the scan spent two hours
    on it. Stop after three in a row and leave the rest for the next run."""
    (paths.master_dir() / "resume.yaml").write_text(FIXTURE.read_text())
    _seed(db_session, 5)
    llm = FakeLLM({"score_posting": lambda user: (_ for _ in ()).throw(LLMOutputError("bad"))})
    stats = score_new_postings(db_session, llm=llm, profile=load_profile())
    assert stats["needs_review"] == 3 and stats["scored"] == 0
    assert stats["aborted"] == "3 consecutive LLM failures"
    rows = db_session.scalars(select(Posting)).all()
    flagged = [p for p in rows if "needs_review" in p.prefilter_result]
    assert len(flagged) == 3
    assert len(llm.calls) == 3


def test_a_success_resets_the_failure_streak(home, db_session) -> None:
    (paths.master_dir() / "resume.yaml").write_text(FIXTURE.read_text())
    _seed(db_session, 5)
    answers = iter(["fail", "fail", "ok", "fail", "fail"])

    def llm_fn(user: str) -> dict:
        if next(answers) == "fail":
            raise LLMOutputError("bad")
        return _good(85)

    stats = score_new_postings(db_session, llm=FakeLLM({"score_posting": llm_fn}),
                               profile=load_profile())
    assert stats["scored"] == 1 and stats["needs_review"] == 4 and "aborted" not in stats
