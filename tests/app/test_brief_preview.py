"""On-demand view of the next brief: the full, uncapped list of what the email would carry,
without sending anything or moving the window the morning brief uses."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jobfinder.db.models import Company, Pipeline, Posting, Run, Score
from jobfinder.db.session import get_engine


def _seed(n: int, scored_at: datetime) -> None:
    with Session(get_engine()) as s:
        co = Company(name="Acme", normalized_name="acme")
        for i in range(n):
            p = Posting(
                company=co, title=f"Analyst Role {i:02d}", normalized_title="analyst",
                apply_url=f"u{i}", dedupe_key=f"k{i}", content_hash="h", status="match",
                city="Montreal", country="CA", description_text="x",
            )
            p.scores.append(Score(
                model="fake", fit_score=90 - i, reasons=["r"], one_line_summary="fit",
                created_at=scored_at,
            ))
            p.pipeline = Pipeline(stage="new")
            s.add(p)
        s.commit()


def test_brief_preview_lists_everything_and_sends_nothing(client) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    _seed(12, now - timedelta(hours=2))
    r = client.get("/brief")
    assert r.status_code == 200 and "srcdoc" in r.text
    for i in range(12):  # the email caps its list at 10; the preview shows all of them
        assert f"Analyst Role {i:02d}" in r.text
    assert "12 matches" in r.text
    with Session(get_engine()) as s:
        assert s.scalar(select(func.count(Run.id)).where(Run.kind == "digest")) == 0


def test_brief_preview_window_defaults_to_last_sent_and_can_widen(client) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    _seed(1, now - timedelta(days=3))
    with Session(get_engine()) as s:
        s.add(Run(kind="digest", status="ok", started_at=now - timedelta(days=1),
                  finished_at=now - timedelta(days=1)))
        s.commit()
    assert "Analyst Role 00" not in client.get("/brief").text
    assert "Analyst Role 00" in client.get("/brief?days=5").text


def test_nav_links_to_the_brief(client) -> None:
    assert 'href="/brief"' in client.get("/").text
