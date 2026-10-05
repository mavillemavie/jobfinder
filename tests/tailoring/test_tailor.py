from pathlib import Path

from sqlalchemy import select

from jobfinder import paths
from jobfinder.config import load_profile
from jobfinder.db.models import Company, Document, Posting, Score
from jobfinder.llm.fake import FakeLLM
from jobfinder.tailoring.master_schema import MasterResume
from jobfinder.tailoring.render_docx import docx_text
from jobfinder.tailoring.tailor import document_lang, file_name, tailor_posting

FIXTURE = Path(__file__).parent.parent / "fixtures" / "master" / "resume.yaml"


def _seed(db_session, lang="en") -> Posting:
    co = Company(name="Acme Logistics Inc.", normalized_name="acme logistics")
    p = Posting(
        company=co, title="Senior Data Analyst", normalized_title="data analyst", apply_url="u",
        dedupe_key="k", content_hash="h", status="match", language=lang,
        description_text=(
            "Requirements: SQL, Power BI, Python, dbt, Snowflake. Bilingual French/English."
        ),
    )
    p.scores.append(Score(model="fake", fit_score=80, reasons=["r"], missing_requirements=["dbt"]))
    db_session.add(p)
    db_session.commit()
    return p


def _tailored(master: MasterResume, **overrides) -> dict:
    resume = master.model_dump(mode="json")
    resume["summary"] = (
        "Senior-level reporting analyst delivering SQL and Power BI reporting for logistics teams."
    )
    out = {
        "resume": resume,
        "cover_letter": (
            "Dear Acme Logistics hiring team,\n\nI build Power BI dashboards.\n\nRegards,\nJordan"
        ),
        "change_log": ["Rewrote summary for the role"],
        "keyword_coverage": {"matched": ["sql", "power bi"], "missing": ["dbt"]},
        "gaps": ["dbt", "Snowflake"],
    }
    out.update(overrides)
    return out


def test_helpers() -> None:
    assert file_name("Resume", "Acme Logistics Inc.", "Jordan-Test-{kind}-{company}") == (
        "Jordan-Test-Resume-AcmeLogisticsInc"
    )
    prof = load_profile(paths.repo_root() / "tests" / "fixtures" / "profile.yaml")
    p = Posting(
        title="x", normalized_title="x", apply_url="u", dedupe_key="k", content_hash="h",
        language="fr",
    )
    assert document_lang(p, prof) == "fr"
    prof.documents.languages = "en"
    assert document_lang(p, prof) == "en"


def test_tailor_posting_creates_four_ready_documents(home, db_session) -> None:
    (paths.master_dir() / "resume.yaml").write_text(FIXTURE.read_text())
    (paths.master_dir() / "cover-letter.md").write_text("Dear Hiring Manager,\n\nGeneral letter.\n")
    master = MasterResume.from_yaml(FIXTURE)
    llm = FakeLLM({"tailor_resume": _tailored(master), "truth_check": {"unsupported_claims": []}})
    p = _seed(db_session)
    docs = tailor_posting(db_session, p.id, llm=llm, profile=load_profile())
    assert {(d.kind, d.format) for d in docs} == {
        ("resume", "docx"), ("resume", "pdf"), ("cover_letter", "docx"), ("cover_letter", "pdf"),
    }
    assert all(d.status == "ready" for d in docs)
    resume_docx = next(d for d in docs if d.kind == "resume" and d.format == "docx")
    assert Path(resume_docx.path).exists()
    assert Path(resume_docx.path).name == "Jordan-Test-Resume-AcmeLogisticsInc.docx"
    assert Path(resume_docx.path).parent == (
        paths.output_dir() / "acme-logistics-inc-senior-data-analyst"
    )
    assert resume_docx.ats_score >= 75 and "dbt" in resume_docx.ats_report["keyword_missing"]
    assert resume_docx.truth_check["ok"] is True
    assert resume_docx.change_log == ["Rewrote summary for the role"]
    assert llm.calls[0]["tier"] == "strong" and "MASTER RESUME" in llm.calls[0]["user"]
    assert "dbt" in llm.calls[0]["user"]
    assert len(db_session.scalars(select(Document)).all()) == 4


def test_truth_finding_marks_needs_review(home, db_session) -> None:
    (paths.master_dir() / "resume.yaml").write_text(FIXTURE.read_text())
    master = MasterResume.from_yaml(FIXTURE)
    bad = _tailored(master)
    bad["resume"]["certifications"].append("AWS Solutions Architect")
    llm = FakeLLM({"tailor_resume": bad, "truth_check": {"unsupported_claims": []}})
    p = _seed(db_session)
    docs = tailor_posting(db_session, p.id, llm=llm, profile=load_profile())
    assert all(d.status == "needs_review" for d in docs)
    assert any("AWS Solutions Architect" in f for f in docs[0].truth_check["deterministic"])
    assert docs[0].ats_report["hold_reasons"] == ["truth"]


def test_regenerate_replaces_previous_documents(home, db_session) -> None:
    (paths.master_dir() / "resume.yaml").write_text(FIXTURE.read_text())
    master = MasterResume.from_yaml(FIXTURE)
    llm = FakeLLM({"tailor_resume": _tailored(master), "truth_check": {"unsupported_claims": []}})
    p = _seed(db_session)
    tailor_posting(db_session, p.id, llm=llm, profile=load_profile())
    tailor_posting(db_session, p.id, llm=llm, profile=load_profile())
    assert len(db_session.scalars(select(Document).where(Document.posting_id == p.id)).all()) == 4


def _setup_master() -> MasterResume:
    (paths.master_dir() / "resume.yaml").write_text(FIXTURE.read_text())
    (paths.master_dir() / "cover-letter.md").write_text(
        "Dear Hiring Manager,\n\nI support 350+ users.\n"
    )
    return MasterResume.from_yaml(FIXTURE)


def test_review_revision_and_report_keys(home, db_session) -> None:
    master = _setup_master()
    revised = _tailored(master, cover_letter="Revised letter for Acme Logistics.\n\nThanks.")
    llm = FakeLLM({
        "tailor_resume": _tailored(master),
        "review_documents": {"score": 81, "critique": ["c1"], "revised": revised},
        "truth_check": {"unsupported_claims": []},
    })
    docs = tailor_posting(db_session, _seed(db_session).id, llm=llm, profile=load_profile())
    rep = next(d for d in docs if d.kind == "resume").ats_report
    assert rep["review"] == {"score": 81, "critique": ["c1"]}
    assert rep["hold_reasons"] == [] and isinstance(rep["budget_cuts"], list)
    assert [c["task"] for c in llm.calls] == ["tailor_resume", "review_documents", "truth_check"]
    assert "Revised letter" in llm.calls[2]["user"]  # truth check sees the final letter
    assert "I support 350+ users." in llm.calls[2]["user"]  # and the master cover letter
    assert "Never introduce an employer" in llm.calls[0]["system"]
    assert "\nPOSTING\nTitle: Senior Data Analyst" in llm.calls[1]["user"]
    assert all(d.status == "ready" for d in docs)
    letter = next(d for d in docs if d.kind == "cover_letter")
    assert letter.ats_report["hold_reasons"] == [] and letter.ats_report["cover_letter_words"] == 6


def test_budget_applied_before_render(home, db_session) -> None:
    master = _setup_master()
    out = _tailored(master)
    out["resume"]["experience"][0]["bullets"] = master.experience[0].bullets * 4  # 8 bullets
    llm = FakeLLM({"tailor_resume": out, "truth_check": {"unsupported_claims": []}})
    docs = tailor_posting(db_session, _seed(db_session).id, llm=llm, profile=load_profile())
    rep = docs[0].ats_report
    assert any(c.startswith("bullets") for c in rep["budget_cuts"])
    assert "error" in rep["review"]  # no canned review → draft kept
    assert docs[0].change_log[-1].startswith("bullets")  # cuts appended to the change log
    text = docx_text(Path(next(d for d in docs if d.format == "docx").path))
    assert text.count("Built 40+ Power BI dashboards") == 3  # 8 alternating bullets cut to 5


def test_long_cover_letter_is_held(home, db_session) -> None:
    master = _setup_master()
    long_letter = "Dear Acme Logistics team,\n\n" + " ".join(["word"] * 360)
    llm = FakeLLM({
        "tailor_resume": _tailored(master, cover_letter=long_letter),
        "truth_check": {"unsupported_claims": []},
    })
    docs = tailor_posting(db_session, _seed(db_session).id, llm=llm, profile=load_profile())
    assert all(d.status == "needs_review" for d in docs)
    assert "cover_letter_length" in docs[0].ats_report["hold_reasons"]


def test_snapshot_released_before_llm_calls(home, db_session, monkeypatch) -> None:
    from jobfinder.tailoring import tailor as tailor_mod

    master = _setup_master()
    events: list[str] = []
    monkeypatch.setattr(tailor_mod, "release_snapshot", lambda s: events.append("release"))

    def _tailor(user: str) -> dict:
        events.append("tailor_resume")
        return _tailored(master)

    llm = FakeLLM({"tailor_resume": _tailor, "truth_check": {"unsupported_claims": []}})
    tailor_posting(db_session, _seed(db_session).id, llm=llm, profile=load_profile())
    assert events[:2] == ["release", "tailor_resume"]
