from sqlalchemy.orm import Session

from jobfinder.db.models import Company, Pipeline, Posting
from jobfinder.db.session import get_engine


def test_board_and_move(client) -> None:
    with Session(get_engine()) as s:
        co = Company(name="Acme", normalized_name="acme")
        p = Posting(
            company=co, title="Data Analyst", normalized_title="data analyst", apply_url="u",
            dedupe_key="k", content_hash="h", status="match",
        )
        p.pipeline = Pipeline(stage="shortlisted")
        s.add(p)
        s.commit()
        pid = p.id
    r = client.get("/pipeline")
    assert r.status_code == 200 and "Data Analyst" in r.text and "shortlisted" in r.text
    r = client.post(f"/pipeline/{pid}/move", data={"stage": "interviewing"}, follow_redirects=False)
    assert r.status_code == 303
    with Session(get_engine()) as s:
        assert s.get(Posting, pid).pipeline.stage == "interviewing"
