from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from jobfinder.db.models import Company, Posting, PostingSource
from jobfinder.discovery import dedupe
from jobfinder.discovery.base import RawPosting
from jobfinder.discovery.dedupe import upsert_raw_postings


def _raw(source="adzuna", sid="1", title="Senior Data Analyst", company="Acme Inc.", **kw):
    kw.setdefault("location_raw", "Montreal, QC")
    kw.setdefault("description", "Build dashboards in Power BI.")
    return RawPosting(source, sid, f"https://{source}.test/{sid}", title, company, **kw)


def test_new_posting_created_with_company_and_source(db_session) -> None:
    stats = upsert_raw_postings(db_session, [_raw()], max_age_days=14)
    assert (stats.new, stats.updated) == (1, 0)
    p = db_session.scalar(select(Posting))
    assert p.normalized_title == "data analyst" and p.seniority == "senior"
    assert p.company.normalized_name == "acme" and p.city == "Montreal" and p.country == "CA"
    assert p.sources[0].source == "adzuna" and p.status == "new" and p.language == "en"


def test_same_job_from_two_sources_merges(db_session) -> None:
    upsert_raw_postings(db_session, [_raw()], max_age_days=14)
    stats = upsert_raw_postings(
        db_session, [_raw(source="linkedin_guest", sid="99")], max_age_days=14
    )
    assert (stats.new, stats.updated) == (0, 1)
    assert db_session.scalar(select(Posting)).sources.__len__() == 2
    assert db_session.scalars(select(PostingSource)).all().__len__() == 2


def test_stale_posting_marked_out(db_session) -> None:
    old = datetime.now(UTC) - timedelta(days=30)
    upsert_raw_postings(db_session, [_raw(posted_at=old)], max_age_days=14)
    p = db_session.scalar(select(Posting))
    assert p.status == "prefiltered_out" and p.prefilter_result["reason"] == "stale"


def test_ats_hint_sets_company_board(db_session) -> None:
    raw = _raw(apply_url="https://boards.greenhouse.io/acme/jobs/5")
    upsert_raw_postings(db_session, [raw], max_age_days=14)
    co = db_session.scalar(select(Company))
    assert (co.ats_type, co.ats_board_token) == ("greenhouse", "acme")


def test_changed_description_flags_rescore(db_session) -> None:
    upsert_raw_postings(db_session, [_raw()], max_age_days=14)
    p = db_session.scalar(select(Posting))
    p.status = "match"
    db_session.commit()
    # Replacement text must be >= len(original "Build dashboards in Power BI." == 29 chars) or
    # the not-shorter-than-existing-complete-description guard (finding 2) will skip the
    # overwrite and this rescore assertion would fail — this is a test-data adjustment only,
    # the assertions below are unchanged.
    upsert_raw_postings(
        db_session,
        [_raw(description="Totally new text about SQL and analytics.")],
        max_age_days=14,
    )
    p = db_session.scalar(select(Posting))
    assert p.prefilter_result.get("rescore") is True and "SQL" in p.description_text


def test_one_bad_item_does_not_discard_the_batch(db_session, monkeypatch) -> None:
    real_normalize_title = dedupe.normalize_title

    def fake_normalize_title(title, *args, **kwargs):
        if "B" in title:
            raise RuntimeError("boom")
        return real_normalize_title(title, *args, **kwargs)

    monkeypatch.setattr(dedupe, "normalize_title", fake_normalize_title)

    raws = [
        _raw(sid="1", title="Data Analyst A"),
        _raw(sid="2", title="Data Analyst B"),
        _raw(sid="3", title="Data Analyst C"),
    ]
    stats = upsert_raw_postings(db_session, raws, max_age_days=14)
    assert stats.new == 2 and stats.errors == 1
    titles = {p.title for p in db_session.scalars(select(Posting)).all()}
    assert titles == {"Data Analyst A", "Data Analyst C"}
    assert len(db_session.scalars(select(Posting)).all()) == 2


def test_shorter_rescrape_does_not_overwrite_complete_description(db_session) -> None:
    long_description = "Build dashboards in Power BI. " * 4  # 120 chars (119 stripped), complete
    upsert_raw_postings(db_session, [_raw(description=long_description)], max_age_days=14)
    p = db_session.scalar(select(Posting))
    p.status = "match"
    db_session.commit()

    short_description = "Short rescrape text."  # 20 chars, still "complete"
    stats = upsert_raw_postings(
        db_session, [_raw(description=short_description)], max_age_days=14
    )

    p = db_session.scalar(select(Posting))
    assert p.description_text == long_description.strip()
    assert not (p.prefilter_result or {}).get("rescore")
    assert stats.updated == 1


def test_unchanged_resubmit_does_not_rescore(db_session) -> None:
    description = "Build dashboards in Power BI."
    upsert_raw_postings(db_session, [_raw(description=description)], max_age_days=14)
    p = db_session.scalar(select(Posting))
    original_content_hash = p.content_hash
    p.status = "match"
    db_session.commit()

    # Resubmit identical raw with same description
    stats = upsert_raw_postings(db_session, [_raw(description=description)], max_age_days=14)

    p = db_session.scalar(select(Posting))
    assert not (p.prefilter_result or {}).get("rescore")
    assert p.content_hash == original_content_hash
    assert stats.updated == 1


def test_retitled_posting_from_same_source_updates_instead_of_failing(db_session) -> None:
    """The employer edits the title; the listing keeps its (source, source_id).

    Two shapes: a retitle that only moves the seniority token (same dedupe_key) and one
    that changes the normalized title (new dedupe_key). The second used to hit the
    (source, source_id) unique constraint every run, so the posting never re-ingested.
    """
    upsert_raw_postings(
        db_session, [_raw(source="jobbank_rss", sid="123", title="Data Analyst")], max_age_days=14
    )
    original_key = db_session.scalar(select(Posting)).dedupe_key

    stats = upsert_raw_postings(
        db_session,
        [_raw(source="jobbank_rss", sid="123", title="Senior Data Analyst")],
        max_age_days=14,
    )
    assert (stats.new, stats.updated, stats.errors) == (0, 1, 0)
    p = db_session.scalar(select(Posting))
    # "Senior" is a seniority token, so normalize_title() strips it and the key is stable —
    # but the stored title and the prefilter-relevant seniority must still track the source.
    assert p.title == "Senior Data Analyst" and p.seniority == "senior"
    assert p.dedupe_key == original_key

    stats = upsert_raw_postings(
        db_session,
        [_raw(source="jobbank_rss", sid="123", title="Data Analyst, Marketing")],
        max_age_days=14,
    )
    assert (stats.new, stats.updated, stats.errors) == (0, 1, 0)
    postings = db_session.scalars(select(Posting)).all()
    assert len(postings) == 1
    p = postings[0]
    assert p.title == "Data Analyst, Marketing"
    assert p.normalized_title == "data analyst marketing"
    assert p.dedupe_key != original_key
    assert len(db_session.scalars(select(PostingSource)).all()) == 1


def test_second_source_merge_does_not_overwrite_the_title(db_session) -> None:
    upsert_raw_postings(
        db_session, [_raw(source="adzuna", sid="1", title="Data Analyst")], max_age_days=14
    )
    upsert_raw_postings(
        db_session,
        [_raw(source="linkedin_guest", sid="99", title="Data Analyst (URGENT!!)")],
        max_age_days=14,
    )
    p = db_session.scalar(select(Posting))
    assert p.title == "Data Analyst" and len(p.sources) == 2
