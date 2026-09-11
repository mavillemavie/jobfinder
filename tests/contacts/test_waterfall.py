import json
from pathlib import Path

import httpx
import respx
from sqlalchemy import select

from jobfinder.config import load_profile
from jobfinder.contacts.waterfall import (
    STEP_ORDER,
    build_clients,
    pending_matches,
    run_for_new_matches,
    run_for_posting,
)
from jobfinder.db.models import Contact, ContactRun, SpendLedger
from jobfinder.llm.fake import FakeLLM
from tests.contacts.conftest import make_ctx, make_posting, make_settings

FIX = Path(__file__).parent.parent / "fixtures" / "contacts"
EXTRACT_PHONE = {"people": [], "emails": [], "phones": ["514-555-0100"], "notes": "footer"}
EXTRACT_EMPTY = {"people": [], "emails": [], "phones": [], "notes": ""}
TITLES = {
    "titles": ["Manager, Analytics", "Director, BI"],
    "talent_title": "Talent Acquisition Partner", "language": "en",
}


def _fix(name: str) -> dict:
    return json.loads((FIX / name).read_text())


def test_step_order_and_build_clients(db_session) -> None:
    assert [s.name for s in STEP_ORDER] == [
        "posting_extract", "target_inference", "company_resolve", "people_search",
        "email_pattern", "email_resolve", "phone", "assemble",
    ]
    p = make_posting(db_session)
    ctx = make_ctx(db_session, p)
    c = build_clients(make_settings(), load_profile(), ctx.http, ctx.budget)
    assert c.apollo is None and c.hunter is None
    assert c.search is not None and c.search.serper is None and c.search.ddg is not None
    profile = load_profile()
    profile.contacts.search_fallback = "none"
    assert build_clients(make_settings(), profile, ctx.http, ctx.budget).search is None
    full = build_clients(
        make_settings(serper_api_key="K", apollo_api_key="AK", hunter_api_key="HK"),
        load_profile(), ctx.http, ctx.budget,
    )
    assert full.search.serper is not None and full.apollo is not None and full.hunter is not None


@respx.mock
def test_full_run_with_keys_stops_early_and_skips_phone(db_session) -> None:
    respx.post("https://google.serper.dev/search").mock(side_effect=[
        httpx.Response(200, json=_fix("serper_search.json")),   # company_resolve
        httpx.Response(200, json=_fix("serper_people.json")),   # people_search query 1
        httpx.Response(200, json={"organic": []}),              # query 2 (2 named < 3)
        httpx.Response(200, json={"organic": []}),              # query 3
    ])
    respx.post(url__regex=r"https://api\.apollo\.io/api/v1/mixed_people/api_search.*").mock(
        return_value=httpx.Response(200, json=_fix("apollo_search.json"))
    )
    respx.get(url__regex=r"https://api\.hunter\.io/v2/domain-search.*").mock(
        return_value=httpx.Response(200, json=_fix("hunter_domain.json"))
    )
    respx.get(url__regex=r"https://api\.hunter\.io/v2/email-verifier.*").mock(
        return_value=httpx.Response(200, json=_fix("hunter_verifier.json"))
    )
    p = make_posting(db_session, domain=None, apply_url="https://boards.greenhouse.io/acme/jobs/1",
                     description="Call 514-555-0100.")
    llm = FakeLLM({"contact_extract": EXTRACT_PHONE, "contact_titles": TITLES})
    settings = make_settings(serper_api_key="K", apollo_api_key="AK", hunter_api_key="HK")
    run = run_for_posting(db_session, p.id, llm=llm, settings=settings, profile=load_profile())
    assert run.status == "ok" and run.finished_at is not None
    names = [s["name"] for s in run.steps]
    assert names == [s.name for s in STEP_ORDER]
    by = {s["name"]: s for s in run.steps}
    assert by["company_resolve"]["notes"]["how"] == "knowledge_graph"
    assert by["people_search"]["credits"] == {"websearch": 3.0}
    assert by["email_pattern"]["credits"] == {"hunter": 1.0}
    assert by["email_resolve"]["notes"]["verified"] == 1
    assert by["phone"]["skipped"] == "stop_when satisfied"
    assert by["assemble"]["notes"]["outcome"] == "people"
    assert run.credits == {"websearch": 4.0, "hunter": 1.5}
    rows = db_session.scalars(select(Contact).where(Contact.posting_id == p.id)
                              .order_by(Contact.confidence.desc())).all()
    assert rows[0].full_name == "Dana Lee" and rows[0].email_status == "verified"
    assert rows[0].phone == "+1 514-555-0100" and rows[0].phone_kind == "switchboard"
    assert p.company.domain == "acmelogistics.example"
    assert p.company.email_pattern == "{first}.{last}"
    ledger = db_session.scalars(select(SpendLedger).where(SpendLedger.posting_id == p.id)).all()
    assert sorted((r.provider, r.credits) for r in ledger) == [
        ("hunter", 0.5), ("hunter", 1.0), ("serper", 1.0), ("serper", 1.0), ("serper", 1.0),
        ("serper", 1.0),
    ]


@respx.mock
def test_keyless_run_uses_ddg_and_contact_page(db_session) -> None:
    respx.get(url__regex=r"https://html\.duckduckgo\.com/html/.*").mock(
        return_value=httpx.Response(200, text=(FIX / "ddg.html").read_text())
    )
    respx.get("https://acmelogistics.example/contact").mock(
        return_value=httpx.Response(200, text='<a href="tel:514-555-0100">Call</a>')
    )
    p = make_posting(db_session, domain=None, apply_url="https://ca.linkedin.com/jobs/view/1")
    llm = FakeLLM({"contact_extract": EXTRACT_EMPTY, "contact_titles": TITLES})
    run = run_for_posting(
        db_session, p.id, llm=llm, settings=make_settings(), profile=load_profile()
    )
    by = {s["name"]: s for s in run.steps}
    assert run.status == "ok"
    assert by["company_resolve"]["notes"]["how"] == "search"
    assert by["email_pattern"]["skipped"] == "no hunter key"
    assert by["email_resolve"]["skipped"] == "no resolvable person" or by["email_resolve"]["ok"]
    assert by["phone"]["notes"]["how"] == "contact_page"
    rows = db_session.scalars(select(Contact).where(Contact.posting_id == p.id)).all()
    assert [r.full_name for r in rows] == ["Dana Lee"]
    assert rows[0].phone == "+1 514-555-0100" and rows[0].phone_kind == "switchboard"
    assert rows[0].confidence == 0.3 and rows[0].email is None
    ledger = db_session.scalars(select(SpendLedger)).all()
    assert {r.provider for r in ledger} == {"websearch"}  # DDG spends job units, no monthly key


def test_nothing_found_yields_needs_manual(db_session) -> None:
    profile = load_profile()
    profile.contacts.search_fallback = "none"
    p = make_posting(db_session, domain=None, website=None,
                     apply_url="https://ca.linkedin.com/jobs/view/1")
    llm = FakeLLM({"contact_extract": EXTRACT_EMPTY, "contact_titles": TITLES})
    run = run_for_posting(db_session, p.id, llm=llm, settings=make_settings(), profile=profile)
    assert run.status == "manual"
    by = {s["name"]: s for s in run.steps}
    assert by["company_resolve"]["skipped"] == "no search backend"
    assert by["assemble"]["notes"]["outcome"] == "needs_manual"
    row = db_session.scalars(select(Contact).where(Contact.posting_id == p.id)).one()
    assert row.evidence["needs_manual"] is True


def test_llm_failure_is_recorded_and_floor_still_written(db_session) -> None:
    profile = load_profile()
    profile.contacts.search_fallback = "none"
    p = make_posting(db_session, domain=None, website=None,
                     apply_url="https://ca.linkedin.com/jobs/view/1")
    run = run_for_posting(
        db_session, p.id, llm=FakeLLM({}), settings=make_settings(), profile=profile
    )
    by = {s["name"]: s for s in run.steps}
    assert by["posting_extract"]["ok"] is False
    assert by["posting_extract"]["error"].startswith("llm:")
    assert by["target_inference"]["ok"] is False
    assert run.status == "manual"
    assert db_session.scalars(select(Contact).where(Contact.posting_id == p.id)).one() is not None


def test_run_for_new_matches_only_touches_matches_without_runs(db_session) -> None:
    profile = load_profile()
    profile.contacts.search_fallback = "none"
    li = "https://ca.linkedin.com/jobs/view/"
    a = make_posting(db_session, dedupe_key="a", domain=None, website=None, apply_url=li + "1")
    b = make_posting(db_session, dedupe_key="b", name="Beta", domain=None, website=None,
                     apply_url=li + "2")
    make_posting(db_session, dedupe_key="c", name="Gamma", domain="g.example", status="scored")
    db_session.add(ContactRun(posting_id=b.id, status="ok"))
    db_session.commit()
    assert [x.id for x in pending_matches(db_session, 10)] == [a.id]
    llm = FakeLLM({"contact_extract": EXTRACT_EMPTY, "contact_titles": TITLES})
    stats = run_for_new_matches(db_session, llm=llm, settings=make_settings(), profile=profile)
    assert stats == {"runs": 1, "ok": 0, "partial": 0, "manual": 1, "error": 0}
    assert run_for_new_matches(db_session, llm=llm, settings=make_settings(), profile=profile) == {
        "runs": 0, "ok": 0, "partial": 0, "manual": 0, "error": 0,
    }


def test_pending_matches_can_select_shortlisted_postings(db_session) -> None:
    """Nightly catch-up when contacts run on shortlist: only shortlisted matches without a run."""
    from jobfinder.db.models import Pipeline

    li = "https://ca.linkedin.com/jobs/view/"
    a = make_posting(db_session, dedupe_key="a", domain=None, website=None, apply_url=li + "1")
    b = make_posting(db_session, dedupe_key="b", name="Beta", domain=None, website=None,
                     apply_url=li + "2")
    a.pipeline = Pipeline(stage="shortlisted")
    b.pipeline = Pipeline(stage="new")
    db_session.commit()
    assert [x.id for x in pending_matches(db_session, 10, stage="shortlisted")] == [a.id]
    assert {x.id for x in pending_matches(db_session, 10)} == {a.id, b.id}


def test_run_survives_a_commit_from_another_connection_during_a_step(db_session) -> None:
    """A Shortlist click or the digest committing while the waterfall is inside an LLM or
    HTTP call must not turn the run into an error (SQLite stale snapshot, 2026-09-09)."""
    from sqlalchemy.orm import Session

    from jobfinder.db.models import Run
    from jobfinder.db.session import get_engine

    def committing_extract(user: str) -> dict:
        other = Session(get_engine(), expire_on_commit=False)
        other.add(Run(kind="digest", status="ok"))
        other.commit()
        other.close()
        return EXTRACT_EMPTY

    profile = load_profile()
    profile.contacts.search_fallback = "none"
    p = make_posting(db_session, domain=None, website=None,
                     apply_url="https://ca.linkedin.com/jobs/view/1")
    llm = FakeLLM({"contact_extract": committing_extract, "contact_titles": TITLES})
    run = run_for_posting(db_session, p.id, llm=llm, settings=make_settings(), profile=profile)
    assert run.status == "manual"
