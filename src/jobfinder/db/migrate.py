from __future__ import annotations

from alembic.config import Config

from alembic import command
from jobfinder import paths
from jobfinder.db.session import default_url


def alembic_config(url: str | None = None) -> Config:
    cfg = Config(str(paths.repo_root() / "alembic.ini"))
    cfg.set_main_option("script_location", str(paths.repo_root() / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url or default_url())
    return cfg


def upgrade_head(url: str | None = None) -> None:
    paths.ensure_dirs()
    command.upgrade(alembic_config(url), "head")
