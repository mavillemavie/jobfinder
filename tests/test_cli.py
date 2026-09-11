from typer.testing import CliRunner

from jobfinder.cli import app


def test_version_prints_version() -> None:
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert "jobfinder 0.1.0" in result.stdout
