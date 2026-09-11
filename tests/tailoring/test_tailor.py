from pathlib import Path

from sqlalchemy import select

from jobfinder import paths
from jobfinder.config import load_profile
from jobfinder.db.models import Company, Document, Posting, Score
from jobfinder.llm.fake import FakeLLM
from jobfinder.tailoring.master_schema import MasterResume
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


def test_regenerate_replaces_previous_documents(home, db_session) -> None:
    (paths.master_dir() / "resume.yaml").write_text(FIXTURE.read_text())
    master = MasterResume.from_yaml(FIXTURE)
    llm = FakeLLM({"tailor_resume": _tailored(master), "truth_check": {"unsupported_claims": []}})
    p = _seed(db_session)
    tailor_posting(db_session, p.id, llm=llm, profile=load_profile())
    tailor_posting(db_session, p.id, llm=llm, profile=load_profile())
    assert len(db_session.scalars(select(Document).where(Document.posting_id == p.id)).all()) == 4
