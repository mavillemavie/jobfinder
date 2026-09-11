from datetime import UTC, datetime

from sqlalchemy.orm import Session

from jobfinder.db.models import Run
from jobfinder.db.session import get_engine


def test_runs_page_lists_runs(client) -> None:
    with Session(get_engine()) as s:
        s.add(Run(
            kind="scan", status="ok", finished_at=datetime.now(UTC).replace(tzinfo=None),
            stats={"sources": {"remotive": 12}, "errors": {"adzuna": "401 from x"},
                   "prefilter": {"ok": 3}},
        ))
        s.commit()
    r = client.get("/runs")
    assert r.status_code == 200 and "remotive" in r.text and "401 from x" in r.text
