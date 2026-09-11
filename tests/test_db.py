from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from jobfinder.db.migrate import upgrade_head
from jobfinder.db.models import Company, Pipeline, Posting, PostingSource
from jobfinder.db.session import get_engine, reset_engine

EXPECTED_TABLES = {
    "companies", "postings", "posting_sources", "scores", "contacts", "contact_runs",
    "documents", "drafts", "pipeline", "activities", "adapter_health", "spend_ledger", "runs",
}


def test_alembic_creates_all_tables(home: Path) -> None:
    url = f"sqlite:///{home / 'data' / 'migrated.db'}"
    upgrade_head(url)
    names = set(inspect(get_engine(url)).get_table_names())
    assert EXPECTED_TABLES <= names
    reset_engine()


def test_posting_roundtrip_and_unique_dedupe(db_session) -> None:
    co = Company(name="Acme Inc", normalized_name="acme")
    db_session.add(co)
    db_session.flush()
    p = Posting(
        company_id=co.id, title="Data Analyst", normalized_title="data analyst",
        apply_url="https://x/1", dedupe_key="k1", content_hash="h1",
    )
    p.sources.append(PostingSource(source="adzuna", source_id="a1", url="https://x/1"))
    p.pipeline = Pipeline(stage="new")
    db_session.add(p)
    db_session.commit()
    assert db_session.get(Posting, p.id).status == "new"
    assert p.pipeline.stage == "new"

    dup = Posting(
        company_id=co.id, title="Data Analyst", normalized_title="data analyst",
        apply_url="https://x/2", dedupe_key="k1", content_hash="h2",
    )
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_busy_timeout_is_30s(db_session) -> None:
    assert db_session.execute(text("PRAGMA busy_timeout")).scalar() == 30000


def test_release_snapshot_lets_a_reader_write_after_another_commit(db_session) -> None:
    """SQLite WAL: a read transaction held across another connection's commit fails on its
    first write (SQLITE_BUSY_SNAPSHOT, never waits). Ending the read first avoids that."""
    from jobfinder.db.models import Run
    from jobfinder.db.session import release_snapshot

    db_session.add(Run(kind="scan", status="ok"))
    db_session.commit()
    run = db_session.scalar(select(Run))  # opens a deferred read transaction (snapshot)
    release_snapshot(db_session)  # the slow call would happen here
    other = Session(get_engine(), expire_on_commit=False)
    other.add(Run(kind="digest", status="ok"))
    other.commit()
    other.close()
    run.status = "error"
    db_session.commit()  # before release_snapshot(): "database is locked"
    assert db_session.get(Run, run.id).status == "error"


def test_stale_snapshot_fails_without_release(db_session) -> None:
    """Characterises the failure the release exists for, so a driver change is noticed."""
    from jobfinder.db.models import Run

    db_session.add(Run(kind="scan", status="ok"))
    db_session.commit()
    run = db_session.scalar(select(Run))
    other = Session(get_engine(), expire_on_commit=False)
    other.add(Run(kind="digest", status="ok"))
    other.commit()
    other.close()
    run.status = "error"
    with pytest.raises(OperationalError, match="database is locked"):
        db_session.commit()
    db_session.rollback()


def test_close_stale_runs_marks_old_running_runs_as_error(db_session) -> None:
    from datetime import UTC, datetime, timedelta

    from jobfinder.db.hygiene import close_stale_runs
    from jobfinder.db.models import Run

    now = datetime.now(UTC).replace(tzinfo=None)
    stale = Run(kind="digest", status="running", started_at=now - timedelta(hours=13))
    fresh = Run(kind="scan", status="running", started_at=now - timedelta(minutes=20))
    done = Run(kind="scan", status="ok", started_at=now - timedelta(days=2))
    db_session.add_all([stale, fresh, done])
    db_session.commit()
    assert close_stale_runs(db_session, hours=12) == 1
    assert stale.status == "error" and stale.stats["error"] == "stale: process restarted"
    assert stale.finished_at is not None
    assert fresh.status == "running" and done.status == "ok"
