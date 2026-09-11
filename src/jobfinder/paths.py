from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def repo_root() -> Path:
    """The git checkout (alembic/, config defaults). Never affected by JOBFINDER_HOME."""
    return _REPO_ROOT


def home() -> Path:
    """Where mutable state lives. Override with JOBFINDER_HOME (tests point it at a tmp dir)."""
    return Path(os.environ.get("JOBFINDER_HOME", _REPO_ROOT))


def config_path() -> Path:
    return home() / "config" / "profile.yaml"


def example_profile_path() -> Path:
    """The tracked template; config/profile.yaml is created from it on first run."""
    return repo_root() / "config" / "profile.example.yaml"


def input_dir() -> Path:
    return home() / "input"


def master_dir() -> Path:
    return home() / "master"


def output_dir() -> Path:
    return home() / "output"


def data_dir() -> Path:
    return home() / "data"


def db_path() -> Path:
    return data_dir() / "jobfinder.db"


def log_dir() -> Path:
    return data_dir() / "logs"


def llm_workdir() -> Path:
    return data_dir() / "llm-workdir"


def llm_cache_dir() -> Path:
    return data_dir() / "llm-cache"


def ensure_dirs() -> None:
    for d in (
        home() / "config",
        input_dir(),
        master_dir(),
        output_dir(),
        data_dir(),
        log_dir(),
        llm_workdir(),
        llm_cache_dir(),
    ):
        d.mkdir(parents=True, exist_ok=True)
