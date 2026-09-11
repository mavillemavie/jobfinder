from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from jobfinder import paths


def configure_logging(level: str = "INFO") -> None:
    """Console + rotating data/logs/jobfinder.log (5 MB × 5). Idempotent."""
    paths.ensure_dirs()
    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = RotatingFileHandler(
        paths.log_dir() / "jobfinder.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
