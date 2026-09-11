from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from typer.testing import CliRunner

from jobfinder.app.digest import build_digest, render_digest, send_digest
from jobfinder.cli import app
from jobfinder.config import load_profile
from jobfinder.db.models import (
    Company,
    Contact,
    Document,
    Pipeline,
    Posting,
    Run,
    Score,
    SpendLedger,
)
from jobfinder.settings import Settings


class FakeSender:
    def __init__(self, fail_times: int = 0) -> None:
        self.sent, self.fail_times = [], fail_times

    def send(self, to, subject, html, text) -> None:
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("smtp down")
        self.sent.append((to, subject, html, text))


def _seed(db_session) -> None:
    co = Company(name="Acme", normalized_name="acme")
    p = Posting(
        company=co, title="Data Analyst", normalized_title="data analyst", apply_url="u",
        dedupe_key="k", content_hash="h", status="match", city="Montreal", country="CA",
        description_text="x",
    )
    p.scores.append(
        Score(model="fake", fit_score=88, reasons=["r"], one_line_summary="Strong Power BI fit")
    )
    p.pipeline = Pipeline(stage="new")
    p.contacts.append(Contact(
        company=co, full_name="Dana Lee", title="Manager", role_kind="hiring_manager",
        email="d@a.x", email_status="verified", confidence=0.85,
    ))
    p.documents.append(
        Document(kind="resume", format="docx", path="/x.docx", status="ready", ats_score=91)
    )
    p.contacts.append(Contact(
        company=co, full_name=None, role_kind="other", phone="+1 514 555 0100",
        phone_kind="switchboard", confidence=0.9, evidence={"switchboard_only": True},
    ))
    db_session.add(p)
    db_session.add(Run(
        kind="scan", status="ok", finished_at=datetime.now(UTC).replace(tzinfo=None),
        stats={"errors": {"adzuna": "401"}, "sources": {"remotive": 3}},
    ))
    db_session.add(SpendLedger(provider="hunter", credits=1, usd_estimate=0.5))
    db_session.commit()


def test_build_and_render(home, db_session) -> None:
    _seed(db_session)
    data = build_digest(db_session, profile=load_profile())
    assert data.counts["matches"] == 1 and data.counts["contacts"] == 2
    assert data.contacts[1]["name"] == "switchboard only"
    assert data.counts["docs_ready"] == 1
    assert data.matches[0]["title"] == "Data Analyst"
    assert data.matches[0]["url"] == "http://localhost:3838/jobs/1"
    assert data.errors == {"adzuna": "401"} and data.spend_mtd == 0.5
    subject, html, text = render_digest(data)
    assert "1 match" in subject and "Dana Lee" in html and "http://localhost:3838/jobs/1" in text
    assert "adzuna" in html


def test_send_digest_records_run_and_retries_once(home, db_session) -> None:
    _seed(db_session)
    settings = Settings(
        _env_file=None, gmail_user="me@gmail.com", gmail_app_password="x", digest_to="me@gmail.com"
    )
    sender = FakeSender(fail_times=1)
    run = send_digest(
        db_session, settings=settings, profile=load_profile(), sender=sender, retry_delay_s=0
    )
    assert run.status == "ok" and len(sender.sent) == 1 and sender.sent[0][0] == "me@gmail.com"
    assert run.stats["attempts"] == 2
    bad = FakeSender(fail_times=2)
    run2 = send_digest(
        db_session, settings=settings, profile=load_profile(), sender=bad, retry_delay_s=0
    )
    assert run2.status == "error" and "smtp down" in run2.stats["error"]


def test_digest_since_last_success(home, db_session) -> None:
    _seed(db_session)
    later = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=1)
    db_session.add(Run(kind="digest", status="ok", started_at=later, finished_at=later))
    db_session.commit()
    data = build_digest(db_session, profile=load_profile())
    assert data.counts["matches"] == 0  # everything is older than the last digest


def test_cli_dry_run(home, db_session) -> None:
    _seed(db_session)
    r = CliRunner().invoke(app, ["digest", "--dry-run"])
    assert r.exit_code == 0 and "Data Analyst" in r.stdout


def _match(db_session, co, title: str, first_seen: datetime, scored_at: datetime, score=88):
    p = Posting(
        company=co, title=title, normalized_title=title.lower(), apply_url=f"u-{title}",
        dedupe_key=f"k-{title}", content_hash="h", status="match", city="Montreal",
        country="CA", description_text="x", first_seen_at=first_seen,
    )
    p.scores.append(Score(
        model="fake", fit_score=score, reasons=["r"], one_line_summary="s", created_at=scored_at,
    ))
    p.pipeline = Pipeline(stage="new")
    db_session.add(p)
    return p


def test_digest_lists_matches_by_the_day_they_matched(home, db_session) -> None:
    """Hydration and scoring are capped per run, so a posting first seen on day 0 is often
    scored on day 3. The brief must key on when it became a match, not when it was seen,
    and must not repeat a match that merely got re-scored."""
    now = datetime.now(UTC).replace(tzinfo=None)
    co = Company(name="Acme", normalized_name="acme")
    backlog = _match(db_session, co, "Backlog", now - timedelta(days=3), now - timedelta(hours=1))
    old = _match(db_session, co, "Old", now - timedelta(days=3), now - timedelta(days=3))
    old.scores.append(Score(  # re-scored today, still a match: not news
        model="fake", fit_score=90, reasons=["r"], one_line_summary="s", created_at=now,
    ))
    _match(db_session, co, "Fresh", now - timedelta(hours=2), now - timedelta(hours=1))
    db_session.commit()
    data = build_digest(db_session, profile=load_profile(), since=now - timedelta(days=1))
    assert sorted(m["title"] for m in data.matches) == ["Backlog", "Fresh"]
    assert data.counts["matches"] == 2 and backlog.id in {m["id"] for m in data.matches}


def test_digest_shows_scored_and_matched_counts_from_the_last_scan(home, db_session) -> None:
    _seed(db_session)
    run = db_session.scalar(select(Run).where(Run.kind == "scan"))
    run.stats = {**run.stats, "scoring": {"scored": 60, "matches": 0}}
    db_session.commit()
    data = build_digest(db_session, profile=load_profile())
    assert data.counts["scored"] == 60 and data.counts["scan_matches"] == 0
    _subject, html, text = render_digest(data)
    assert "scored 60" in html and "0 matched" in html and "scored 60" in text


class CommittingSender(FakeSender):
    """Sends fine, but another connection commits while it is sending — the scan thread
    finishing a posting while the digest is inside the 30 s SMTP call (or its retry sleep)."""

    def send(self, to, subject, html, text) -> None:
        from sqlalchemy.orm import Session

        from jobfinder.db.session import get_engine

        other = Session(get_engine(), expire_on_commit=False)
        other.add(Run(kind="scan", status="ok"))
        other.commit()
        other.close()
        super().send(to, subject, html, text)


def test_send_digest_survives_a_commit_from_another_connection(home, db_session) -> None:
    _seed(db_session)
    settings = Settings(
        _env_file=None, gmail_user="me@gmail.com", gmail_app_password="x", digest_to="me@gmail.com"
    )
    sender = CommittingSender()
    run = send_digest(
        db_session, settings=settings, profile=load_profile(), sender=sender, retry_delay_s=0
    )
    assert run.status == "ok" and len(sender.sent) == 1
    db_session.expire_all()
    assert db_session.get(Run, run.id).status == "ok"
