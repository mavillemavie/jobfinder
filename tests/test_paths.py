from pathlib import Path

from jobfinder import paths


def test_home_defaults_to_repo_root() -> None:
    assert (paths.home() / "pyproject.toml").exists()
    assert paths.repo_root() == paths.home()


def test_home_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JOBFINDER_HOME", str(tmp_path))
    assert paths.home() == tmp_path
    assert paths.db_path() == tmp_path / "data" / "jobfinder.db"
    assert paths.repo_root() != tmp_path  # repo root never moves
    paths.ensure_dirs()
    assert (tmp_path / "data" / "llm-workdir").is_dir()
