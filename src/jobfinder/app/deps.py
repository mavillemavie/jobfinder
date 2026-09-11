from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from jobfinder.config import Profile, load_profile
from jobfinder.db.session import get_engine
from jobfinder.settings import Settings, get_settings

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def ago(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    now = datetime.now(UTC).replace(tzinfo=None)
    delta = now - dt.replace(tzinfo=None)
    days, hours = delta.days, delta.seconds // 3600
    if days > 0:
        return f"{days}d"
    return f"{hours}h" if hours else f"{delta.seconds // 60}m"


def pct(x: float | None) -> str:
    return "—" if x is None else f"{round(x * 100)}%"


def score_class(score: int | None) -> str:
    if score is None:
        return "score-none"
    return "score-high" if score >= 85 else "score-good" if score >= 70 else "score-low"


templates.env.filters.update({"ago": ago, "pct": pct, "score_class": score_class})


def db_session() -> Iterator[Session]:
    session = Session(get_engine(), expire_on_commit=False)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def current_profile() -> Profile:
    return load_profile()


def current_settings() -> Settings:
    return get_settings()


def get_llm_dep(request: Request):  # noqa: ANN201
    llm = getattr(request.app.state, "llm", None)
    if llm is None:
        from jobfinder.llm import get_llm

        llm = get_llm(get_settings())
        request.app.state.llm = llm
    return llm
