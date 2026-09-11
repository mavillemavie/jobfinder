from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session
from typer.testing import CliRunner

from jobfinder.cli import app
from jobfinder.db.models import (
    Company,
    Contact,
    ContactRun,
    Document,
    Posting,
    Run,
    Score,
    SpendLedger,
)
from jobfinder.db.session import get_engine
from jobfinder.stats import measurement_summary


def _seed() -> None:
    with Session(get_engine()) as s:
        co = Company(name="Acme", normalized_name="acme")
        rows = []
        for i, (status, email_status, name) in enumerate([
            ("match", "verified", "Dana Lee"), ("match", "unverified", None),
            ("scored", None, None),
        ]):
            p = Posting(
                company=co, title=f"Analyst {i}", normalized_title="analyst", apply_url="u",
                dedupe_key=f"k{i}", content_hash=f"h{i}", status=status, description_text="x",
            )
            p.scores.append(Score(model="fake", fit_score=80 - i * 10, reasons=["r"]))
            if status == "match":
                p.contacts.append(Contact(
                    company=co, full_name=name, email="d@a.x" if name else None,
                    email_status=email_status, phone="+1 514 555 0100",
                    phone_kind="direct" if name else "switchboard", confidence=0.8,
                    evidence={} if name else {"switchboard_only": True},
                ))
            rows.append(p)
        s.add_all(rows)
        s.flush()
        s.add(ContactRun(posting_id=rows[0].id, status="ok", credits={"hunter": 1.5}))
        s.add(ContactRun(posting_id=rows[1].id, status="ok", credits={"websearch": 4.0}))
        rows[0].documents.append(
            Document(kind="resume", format="docx", path="/x.docx", status="ready", ats_score=90)
        )
        now = datetime.now(UTC).replace(tzinfo=None)
        s.add(Run(kind="scan", status="ok", started_at=now, finished_at=now,
                  stats={"sources": {"remotive": 5}, "scoring": {"scored": 3, "matches": 2}}))
        s.add(Run(kind="digest", status="error", started_at=now, finished_at=now,
                  stats={"error": "no gmail"}))
        s.add(SpendLedger(provider="hunter", credits=1.5, usd_estimate=0.0))
        s.commit()


def test_measurement_summary_counts(home) -> None:
    _seed()
    with Session(get_engine()) as s:
        m = measurement_summary(s, since=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1))
    assert m["scans"] == {"total": 1, "ok": 1, "error": 0}
    assert m["digests"] == {"total": 1, "ok": 0, "error": 1}
    assert m["postings"]["total"] == 3 and m["postings"]["match"] == 2
    assert m["contacts"] == {
        "matches_with_run": 2, "named_person": 1, "verified_email": 1, "any_phone": 2,
        "direct_phone": 1, "switchboard_only": 1, "needs_manual": 0,
    }
    assert m["contact_blockers"] == {"no_named_person": 1, "no_email_found": 0}
    assert m["documents"] == {"ready": 1, "needs_review": 0}
    assert m["credits"] == {"hunter": 1.5, "websearch": 4.0} and m["spend_usd"] == 0.0


def test_stats_cli(home) -> None:
    _seed()
    r = CliRunner().invoke(app, ["stats", "--days", "1"])
    assert r.exit_code == 0, r.output
    needles = ("scans", "matches", "named person", "verified email", "no_named_person", "spend")
    for needle in needles:
        assert needle in r.stdout, needle
