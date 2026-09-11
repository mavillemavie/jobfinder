from typer.testing import CliRunner

from jobfinder.cli import app


def test_doctor_reports_checks(home, monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    r = CliRunner().invoke(app, ["doctor"])
    assert r.exit_code == 0, r.output
    for needle in ("database", "master resume", "claude CLI", "adzuna", "gmail", "port"):
        assert needle in r.stdout, needle
