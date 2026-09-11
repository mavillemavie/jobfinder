from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session

from jobfinder import paths

_engines: dict[str, Engine] = {}


def default_url() -> str:
    return f"sqlite:///{paths.db_path()}"


def get_engine(url: str | None = None) -> Engine:
    url = url or default_url()
    if url not in _engines:
        # busy_timeout 30 s: a genuine writer/writer overlap (scan thread vs. a dashboard
        # click) waits instead of failing after pysqlite's 5 s default. It does NOT cover
        # a stale read snapshot — see release_snapshot().
        engine = create_engine(url, future=True, connect_args={"timeout": 30})

        # SQLAlchemy's documented pysqlite recipe ("Serializable isolation / savepoints /
        # transactional DDL"). The driver otherwise opens its own implicit transaction at
        # the wrong moment, which breaks SAVEPOINT — the mechanism dedupe relies on to
        # isolate one bad raw posting from the rest of the batch. Disable the driver's
        # implicit BEGIN and emit it ourselves instead.
        @event.listens_for(engine, "connect")
        def _no_implicit_begin(dbapi_conn, _record) -> None:  # noqa: ANN001
            dbapi_conn.isolation_level = None

        @event.listens_for(engine, "begin")
        def _begin(conn) -> None:  # noqa: ANN001
            conn.exec_driver_sql("BEGIN")

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _record) -> None:  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

        _engines[url] = engine
    return _engines[url]


def reset_engine() -> None:
    for engine in _engines.values():
        engine.dispose()
    _engines.clear()


def release_snapshot(session: Session) -> None:
    """End the current transaction before a slow call (LLM, SMTP, HTTP, sleep).

    In WAL mode a connection that opened a read transaction and then waits fails on its
    first write with "database is locked" the moment any other connection has committed
    in between (SQLITE_BUSY_SNAPSHOT — the busy timeout is never consulted). Committing
    here ends the read snapshot; pending changes, if any, are persisted; the write after
    the slow call starts a fresh transaction. Objects stay usable (expire_on_commit=False).
    """
    session.commit()


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    session = Session(engine or get_engine(), expire_on_commit=False)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
