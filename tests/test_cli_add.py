from pathlib import Path

import httpx
import respx
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from jobfinder import paths
from jobfinder.cli import app
from jobfinder.db.models import Posting
from jobfinder.db.session import get_engine
from jobfinder.llm.fake import FakeLLM

FIX = Path(__file__).parent / "fixtures" / "manual"
GH = "https://boards.greenhouse.io/acme/jobs/7"
SCORE = {
    "fit_score": 81, "reasons": ["bi"], "missing_requirements": [], "seniority_match": "match",
    "eligibility": {
        "remote_from_canada": "unclear", "uk_right_to_work_required": "no",
        "sponsorship_mentioned": "no",
    },
    "language": "en", "red_flags": [], "one_line_summary": "good",
}


def _master() -> None:
    src = paths.repo_root() / "tests/fixtures/master/resume.yaml"
    (paths.master_dir() / "resume.yaml").write_text(
        src.read_text(encoding="utf-8"), encoding="utf-8"
    )


def _jsonld() -> str:
    return (FIX / "greenhouse_jsonld.html").read_text(encoding="utf-8")


@respx.mock
def test_cli_add_saves_shortlists_and_scores(home, monkeypatch) -> None:
    _master()
    fake = FakeLLM({"score_posting": SCORE})
    monkeypatch.setattr("jobfinder.llm.get_llm", lambda settings, **kw: fake)
    respx.get(GH).mock(return_value=httpx.Response(200, text=_jsonld()))
    result = CliRunner().invoke(app, ["add", GH])
    assert result.exit_code == 0, result.stdout
    assert "Data Analyst" in result.stdout and "jsonld" in result.stdout and "81" in result.stdout
    with Session(get_engine()) as s:
        assert s.query(Posting).one().pipeline.stage == "shortlisted"


@respx.mock
def test_cli_add_text_file_rescues_a_blocked_page(home, monkeypatch, tmp_path) -> None:
    _master()
    fake = FakeLLM({
        "extract_posting": {"title": "Data Analyst", "company": "Acme", "location": ""},
        "score_posting": SCORE,
    })
    monkeypatch.setattr("jobfinder.llm.get_llm", lambda settings, **kw: fake)
    respx.get(GH).mock(return_value=httpx.Response(403, text="denied"))
    desc = tmp_path / "desc.txt"
    desc.write_text("Data Analyst at Acme. Build dashboards. " * 20, encoding="utf-8")
    result = CliRunner().invoke(app, ["add", GH, "--text-file", str(desc)])
    assert result.exit_code == 0, result.stdout
    assert "403" in result.stdout and "llm" in result.stdout


@respx.mock
def test_cli_add_blocked_without_text_exits_1(home, monkeypatch) -> None:
    monkeypatch.setattr("jobfinder.llm.get_llm", lambda settings, **kw: FakeLLM())
    respx.get(GH).mock(return_value=httpx.Response(403, text="denied"))
    result = CliRunner().invoke(app, ["add", GH])
    assert result.exit_code == 1 and "--text-file" in result.stdout


def test_cli_add_bad_url_exits_2(home, monkeypatch) -> None:
    monkeypatch.setattr("jobfinder.llm.get_llm", lambda settings, **kw: FakeLLM())
    result = CliRunner().invoke(app, ["add", "ftp://nope"])
    assert result.exit_code == 2 and "http(s)" in result.stdout


def test_cli_add_missing_text_file_exits_2(home, monkeypatch) -> None:
    monkeypatch.setattr("jobfinder.llm.get_llm", lambda settings, **kw: FakeLLM())
    result = CliRunner().invoke(app, ["add", GH, "--text-file", "/nonexistent/desc.txt"])
    assert result.exit_code == 2 and "desc.txt" in result.stdout
