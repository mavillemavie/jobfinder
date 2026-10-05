from sqlalchemy.orm import Session
from typer.testing import CliRunner

from jobfinder import paths
from jobfinder.cli import app
from jobfinder.db.models import Company, Posting
from jobfinder.db.session import get_engine
from jobfinder.llm.fake import FakeLLM
from jobfinder.tailoring.master_schema import MasterResume

MASTER = paths.repo_root() / "tests/fixtures/master/resume.yaml"


def _setup(fake_review: dict | None) -> tuple[int, FakeLLM]:
    (paths.master_dir() / "resume.yaml").write_text(
        MASTER.read_text(encoding="utf-8"), encoding="utf-8"
    )
    master = MasterResume.from_yaml(MASTER)
    draft = {
        "resume": master.model_dump(mode="json"), "cover_letter": "Dear team,\n\nHi.",
        "change_log": [], "keyword_coverage": {"matched": [], "missing": []}, "gaps": [],
    }
    responses = {"tailor_resume": draft, "truth_check": {"unsupported_claims": []}}
    if fake_review is not None:
        responses["review_documents"] = {**fake_review, "revised": draft}
    with Session(get_engine()) as s:
        p = Posting(
            company=Company(name="Acme", normalized_name="acme"), title="Data Analyst",
            normalized_title="data analyst", apply_url="u", dedupe_key="k", content_hash="h",
            status="match", description_text="SQL and Power BI.",
        )
        s.add(p)
        s.commit()
        return p.id, FakeLLM(responses)


def test_tailor_prints_review_score(home, monkeypatch) -> None:
    pid, fake = _setup({"score": 77, "critique": ["Lead with SQL"]})
    monkeypatch.setattr("jobfinder.llm.get_llm", lambda settings, **kw: fake)
    result = CliRunner().invoke(app, ["tailor", str(pid)])
    assert result.exit_code == 0, result.stdout
    assert "review 77/100" in result.stdout and "Lead with SQL" in result.stdout


def test_tailor_prints_review_skip(home, monkeypatch) -> None:
    pid, fake = _setup(None)
    monkeypatch.setattr("jobfinder.llm.get_llm", lambda settings, **kw: fake)
    result = CliRunner().invoke(app, ["tailor", str(pid)])
    assert result.exit_code == 0, result.stdout
    assert "review skipped" in result.stdout


def test_tailor_output_is_not_rich_markup(home, monkeypatch) -> None:
    pid, fake = _setup({"score": 60, "critique": ["remove the [Name] placeholder [/b]"]})
    monkeypatch.setattr("jobfinder.llm.get_llm", lambda settings, **kw: fake)
    result = CliRunner().invoke(app, ["tailor", str(pid)])
    assert result.exit_code == 0, result.stdout
    assert "remove the [Name] placeholder [/b]" in result.stdout
