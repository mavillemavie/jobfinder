"""A rejected job must not come back as a different row (Plan 8, spec 2026-09-06)."""
from sqlalchemy import select

from jobfinder.db.models import Pipeline, Posting
from jobfinder.discovery.base import RawPosting
from jobfinder.discovery.dedupe import upsert_raw_postings
from jobfinder.discovery.twins import blocking_twin, scope_for


def _raw(sid: str, title: str = "Data Analyst", company: str = "Autodesk", city: str = "Toronto"):
    return RawPosting(
        "adzuna", sid, f"https://a.test/{sid}", title, company,
        location_raw=f"{city}, Canada", description="SQL Power BI dashboards",
    )


def _seed_dismissed(db_session, reason: str, title: str = "Data Analyst") -> Posting:
    upsert_raw_postings(db_session, [_raw("1", title=title)], max_age_days=14)
    p = db_session.scalar(select(Posting))
    p.status, p.dismiss_reason = "dismissed", reason
    db_session.commit()
    return p


def test_scope_for_reason() -> None:
    assert scope_for("bad_company") == "company"
    assert scope_for("wrong_location") == "row"
    assert scope_for("too_senior") == "title" and scope_for("other") == "title"
    assert scope_for("rejected") == "title"


def test_title_twin_of_a_dismissed_posting_is_inserted_dismissed(db_session) -> None:
    twin = _seed_dismissed(db_session, "too_senior")
    stats = upsert_raw_postings(db_session, [_raw("2", city="Vancouver")], max_age_days=14)
    assert stats.new == 1 and stats.dismissed_twin == 1
    rows = db_session.scalars(select(Posting).order_by(Posting.id)).all()
    assert len(rows) == 2 and rows[1].city == "Vancouver"
    assert rows[1].status == "dismissed" and rows[1].dismiss_reason == "too_senior"
    assert rows[1].prefilter_result == {
        "passed": False, "reason": "dismissed_twin", "twin_id": twin.id
    }


def test_wrong_location_dismissal_does_not_block_the_other_city(db_session) -> None:
    _seed_dismissed(db_session, "wrong_location")
    stats = upsert_raw_postings(db_session, [_raw("2", city="Vancouver")], max_age_days=14)
    assert stats.dismissed_twin == 0
    assert db_session.scalars(select(Posting).order_by(Posting.id)).all()[1].status == "new"


def test_bad_company_blocks_every_title_from_that_company(db_session) -> None:
    _seed_dismissed(db_session, "bad_company")
    stats = upsert_raw_postings(
        db_session, [_raw("2", title="BI Developer"), _raw("3", company="Shopify")],
        max_age_days=14,
    )
    assert stats.dismissed_twin == 1
    rows = {p.title: p for p in db_session.scalars(select(Posting))}
    assert rows["BI Developer"].status == "dismissed"
    assert rows["BI Developer"].dismiss_reason == "bad_company"
    shopify = [p for p in rows.values() if p.company.name == "Shopify"][0]
    assert shopify.status == "new"


def test_other_title_is_not_blocked_by_a_title_scoped_dismissal(db_session) -> None:
    _seed_dismissed(db_session, "wrong_title")
    upsert_raw_postings(db_session, [_raw("2", title="BI Developer")], max_age_days=14)
    assert db_session.scalars(select(Posting).order_by(Posting.id)).all()[1].status == "new"


def test_pipeline_rejected_twin_blocks_with_reason_rejected(db_session) -> None:
    upsert_raw_postings(db_session, [_raw("1")], max_age_days=14)
    p = db_session.scalar(select(Posting))
    p.status = "match"
    p.pipeline = Pipeline(stage="closed", close_reason="rejected")
    db_session.commit()
    assert blocking_twin(db_session, p.company_id, "data analyst") is p
    assert blocking_twin(db_session, p.company_id, "bi developer") is None
    stats = upsert_raw_postings(db_session, [_raw("2", city="Montreal")], max_age_days=14)
    assert stats.dismissed_twin == 1
    new = db_session.scalars(select(Posting).order_by(Posting.id)).all()[1]
    assert new.status == "dismissed" and new.dismiss_reason == "rejected"


def test_a_known_row_reseen_keeps_its_dismissal(db_session) -> None:
    p = _seed_dismissed(db_session, "other")
    stats = upsert_raw_postings(db_session, [_raw("1")], max_age_days=14)
    assert stats.updated == 1 and stats.dismissed_twin == 0
    assert db_session.get(Posting, p.id).status == "dismissed"
