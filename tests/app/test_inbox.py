from sqlalchemy import select
from sqlalchemy.orm import Session

from jobfinder.db.models import Activity, Company, Pipeline, Posting, Score
from jobfinder.db.session import get_engine


def _seed(n: int = 2) -> list[int]:
    with Session(get_engine()) as s:
        co = Company(name="Acme", normalized_name="acme")
        ids = []
        for i in range(n):
            p = Posting(
                company=co, title=f"Data Analyst {i}", normalized_title="data analyst",
                apply_url="u", dedupe_key=f"k{i}", content_hash=f"h{i}", status="match",
                city="Montreal", country="CA",
                prefilter_result={"passed": True, "reason": "ok", "location_key": "montreal"},
                description_text="SQL",
            )
            p.scores.append(
                Score(
                    model="fake", fit_score=90 - i * 30, reasons=["good fit"], one_line_summary="s"
                )
            )
            p.pipeline = Pipeline(stage="new")
            s.add(p)
            s.flush()
            ids.append(p.id)
        s.commit()
        return ids


def test_inbox_lists_matches_sorted_and_filters(client) -> None:
    _seed()
    r = client.get("/")
    assert r.status_code == 200
    assert r.text.index("Data Analyst 0") < r.text.index("Data Analyst 1")
    assert "good fit" in r.text
    r2 = client.get("/?min_score=80")
    assert "Data Analyst 0" in r2.text and "Data Analyst 1" not in r2.text
    assert "Data Analyst 1" in client.get("/?q=Analyst%201").text


def test_shortlist_and_dismiss_actions(client) -> None:
    a, b = _seed()
    r = client.post(f"/postings/{a}/shortlist")
    assert r.status_code == 200 and "shortlisted" in r.text
    r = client.post(f"/postings/{b}/dismiss", data={"reason": "too_senior"})
    assert r.status_code == 200 and "dismissed" in r.text
    with Session(get_engine()) as s:
        assert s.get(Posting, a).pipeline.stage == "shortlisted"
        pb = s.get(Posting, b)
        assert pb.status == "dismissed" and pb.dismiss_reason == "too_senior"
        assert pb.pipeline.stage == "closed" and pb.pipeline.close_reason == "withdrawn"
        changes = s.scalars(select(Activity).where(Activity.kind == "stage_change")).all()
        assert len(changes) == 2


def test_rescore_uses_llm_and_returns_row(client, fake_llm) -> None:
    from jobfinder import paths

    fixture = paths.repo_root() / "tests/fixtures/master/resume.yaml"
    (paths.master_dir() / "resume.yaml").write_text(fixture.read_text())
    (a, _) = _seed()
    fake_llm.responses["score_posting"] = {
        "fit_score": 55, "reasons": ["meh"], "missing_requirements": [],
        "seniority_match": "match",
        "eligibility": {
            "remote_from_canada": "unclear", "uk_right_to_work_required": "no",
            "sponsorship_mentioned": "no",
        },
        "language": "en", "red_flags": [], "one_line_summary": "meh",
    }
    r = client.post(f"/postings/{a}/rescore")
    assert r.status_code == 200 and "55" in r.text


def _add_title(company_of: int, title: str, norm: str, status: str = "match") -> int:
    with Session(get_engine()) as s:
        p = Posting(
            company=s.get(Posting, company_of).company, title=title, normalized_title=norm,
            apply_url="u", dedupe_key=f"k-{norm}", content_hash="h", status=status,
            description_text="x", prefilter_result={"passed": True, "reason": "ok"},
        )
        s.add(p)
        s.commit()
        return p.id


def test_dismiss_sweeps_title_twins_but_not_other_titles(client) -> None:
    a, b = _seed()  # same company, same normalized title, two rows
    other = _add_title(a, "BI Developer", "bi developer")
    r = client.post(f"/postings/{a}/dismiss", data={"reason": "too_senior"})
    assert r.status_code == 200 and "dismissed" in r.text and "1 twin" in r.text
    with Session(get_engine()) as s:
        pb = s.get(Posting, b)
        assert pb.status == "dismissed" and pb.dismiss_reason == "too_senior"
        assert pb.pipeline.stage == "closed" and pb.pipeline.close_reason == "withdrawn"
        assert pb.prefilter_result["twin_of"] == a and pb.prefilter_result["passed"] is True
        assert s.get(Posting, other).status == "match"
    assert "Data Analyst" not in client.get("/").text and "BI Developer" in client.get("/").text


def test_dismiss_bad_company_sweeps_every_title(client) -> None:
    a, b = _seed()
    other = _add_title(a, "BI Developer", "bi developer", status="new")
    r = client.post(f"/postings/{a}/dismiss", data={"reason": "bad_company"})
    assert "2 twins" in r.text
    with Session(get_engine()) as s:
        assert s.get(Posting, b).status == "dismissed"
        po = s.get(Posting, other)
        assert po.status == "dismissed" and po.dismiss_reason == "bad_company"


def test_dismiss_wrong_location_sweeps_nothing(client) -> None:
    a, b = _seed()
    r = client.post(f"/postings/{a}/dismiss", data={"reason": "wrong_location"})
    assert "dismissed" in r.text and "twin" not in r.text
    with Session(get_engine()) as s:
        assert s.get(Posting, b).status == "match"


def test_dismiss_does_not_sweep_a_twin_jf_already_advanced(client) -> None:
    a, b = _seed()
    client.post(f"/postings/{a}/shortlist")
    r = client.post(f"/postings/{b}/dismiss", data={"reason": "too_senior"})
    assert "dismissed" in r.text and "twin" not in r.text
    with Session(get_engine()) as s:
        pa = s.get(Posting, a)
        assert pa.status == "match" and pa.pipeline.stage == "shortlisted"


def test_shortlist_starts_a_contact_run_once(client, monkeypatch) -> None:
    """Contacts cost Hunter credits; they run for the jobs JF shortlists, not for every match."""
    from jobfinder.app.routes import inbox as inbox_routes

    started: list[int] = []
    monkeypatch.setattr(inbox_routes, "start_contacts_thread", lambda pid, llm: started.append(pid))
    a, b = _seed()
    r = client.post(f"/postings/{a}/shortlist")
    assert r.status_code == 200 and "finding contact" in r.text
    assert started == [a]
    client.post(f"/postings/{a}/shortlist")  # again: no second run while one exists / is running
    assert started == [a]
    with Session(get_engine()) as s:  # a posting that already has a contact run is left alone
        from jobfinder.db.models import ContactRun
        s.add(ContactRun(posting_id=b, status="ok"))
        s.commit()
    r = client.post(f"/postings/{b}/shortlist")
    assert "shortlisted" in r.text and "finding contact" not in r.text and started == [a]


def test_shortlist_trigger_can_be_turned_off(client, monkeypatch) -> None:
    from jobfinder.app.routes import inbox as inbox_routes
    from jobfinder.config import load_profile, save_profile

    prof = load_profile()
    prof.contacts.run_on_shortlist = False
    save_profile(prof)
    started: list[int] = []
    monkeypatch.setattr(inbox_routes, "start_contacts_thread", lambda pid, llm: started.append(pid))
    a, _b = _seed()
    r = client.post(f"/postings/{a}/shortlist")
    assert "shortlisted" in r.text and started == []


def _seed_seen() -> None:
    """Two matches that disagree on score and age: the older one scores higher."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    with Session(get_engine()) as s:
        co = Company(name="Acme", normalized_name="acme")
        for i, (title, score, seen) in enumerate([
            ("Old High", 90, now - timedelta(days=3)), ("New Low", 60, now),
        ]):
            p = Posting(
                company=co, title=title, normalized_title="data analyst", apply_url="u",
                dedupe_key=f"s{i}", content_hash=f"sh{i}", status="match", city="Montreal",
                country="CA", description_text="SQL", first_seen_at=seen,
                prefilter_result={"passed": True, "reason": "ok", "location_key": "montreal"},
            )
            p.scores.append(
                Score(model="fake", fit_score=score, reasons=["r"], one_line_summary="s")
            )
            p.pipeline = Pipeline(stage="new")
            s.add(p)
        s.commit()


def test_inbox_sorts_by_seen_and_score_in_both_directions(client) -> None:
    _seed_seen()
    t = client.get("/").text  # default: score, highest first
    assert t.index("Old High") < t.index("New Low")
    t = client.get("/?sort=seen").text  # newest first
    assert t.index("New Low") < t.index("Old High")
    t = client.get("/?sort=seen&dir=asc").text  # oldest first
    assert t.index("Old High") < t.index("New Low")
    t = client.get("/?sort=score&dir=asc").text  # lowest first
    assert t.index("New Low") < t.index("Old High")


def test_inbox_unknown_sort_falls_back_to_default(client) -> None:
    _seed_seen()
    r = client.get("/?sort=bogus&dir=sideways")
    assert r.status_code == 200
    assert r.text.index("Old High") < r.text.index("New Low")


def test_inbox_sort_headers_toggle_and_keep_filters(client) -> None:
    _seed_seen()
    t = client.get("/?status=match&min_score=50&sort=seen").text
    # the active column's link flips direction, the other column starts descending
    assert "sort=seen&amp;dir=asc" in t and "sort=score&amp;dir=desc" in t
    assert t.count("min_score=50") >= 2  # both header links carry the filters
    # re-filtering keeps the chosen sort
    assert 'name="sort" value="seen"' in t and 'name="dir" value="desc"' in t


def test_inbox_empty_min_score_is_no_minimum(client) -> None:
    _seed_seen()
    r = client.get("/?status=match&min_score=&location=&q=")  # the filter form with an empty box
    assert r.status_code == 200
    assert "Old High" in r.text and "New Low" in r.text


def test_inbox_sort_links_omit_empty_filters(client) -> None:
    _seed_seen()
    t = client.get("/?sort=seen").text
    assert "min_score=&amp;" not in t and "location=&amp;" not in t and "q=&amp;" not in t
    assert "/?status=match&amp;sort=score&amp;dir=desc" in t
