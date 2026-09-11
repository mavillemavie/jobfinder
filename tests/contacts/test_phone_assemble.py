import json
from pathlib import Path

import httpx
import respx
from sqlalchemy import select

from jobfinder.config import load_profile
from jobfinder.contacts.base import Person
from jobfinder.contacts.budget import Budget
from jobfinder.contacts.providers.search import SerperClient, WebSearch
from jobfinder.contacts.steps.assemble import AssembleStep, is_generic_email
from jobfinder.contacts.steps.phone import PhoneStep
from jobfinder.db.models import Contact
from jobfinder.discovery.fetch import HttpClient
from tests.contacts.conftest import make_ctx, make_posting

FIX = Path(__file__).parent.parent / "fixtures" / "contacts"


def test_phone_uses_cache_then_already_found(db_session) -> None:
    p = make_posting(db_session)
    p.company.main_phone = "+1 514-555-0100"
    ctx = make_ctx(db_session, p)
    res = PhoneStep().run(ctx)
    assert res.notes["how"] == "cached"
    assert ctx.phones == [("+1 514-555-0100", "switchboard", "company_cache")]
    p2 = make_posting(db_session, dedupe_key="k2", name="Beta Inc", domain="beta.example")
    ctx2 = make_ctx(db_session, p2)
    ctx2.phones = [("+1 514-555-0199", "switchboard", "posting")]
    assert PhoneStep().run(ctx2).notes["how"] == "already_found"


def test_phone_reads_knowledge_graph_without_a_query(db_session) -> None:
    p = make_posting(db_session)
    ctx = make_ctx(db_session, p)
    ctx.knowledge_graph = {"attributes": {"Phone": "+1 514-555-0100"}}
    res = PhoneStep().run(ctx)
    assert res.notes["how"] == "knowledge_graph" and res.credits == {}
    assert ctx.phones == [("+1 514-555-0100", "switchboard", "serper_kg")]
    assert p.company.main_phone == "+1 514-555-0100"


@respx.mock
def test_phone_contact_page_then_search_fallback(db_session) -> None:
    respx.get("https://acmelogistics.example/contact").mock(
        return_value=httpx.Response(200, text='<a href="tel:+1-514-555-0100">Call</a>')
    )
    p = make_posting(db_session)
    ctx = make_ctx(db_session, p)
    res = PhoneStep().run(ctx)
    assert res.notes["how"] == "contact_page"
    assert ctx.phones == [("+1 514-555-0100", "switchboard", "https://acmelogistics.example/contact")]

    for path in ("contact", "contact-us", "contactez-nous", "nous-joindre", ""):
        respx.get(f"https://beta.example/{path}").mock(return_value=httpx.Response(404))
    serper = respx.post("https://google.serper.dev/search").mock(
        return_value=httpx.Response(200, json={"organic": [
            {"title": "Beta Inc - Contact", "link": "https://beta.example/x",
             "snippet": "Reach us at (514) 555-0177 weekdays."}]})
    )
    p2 = make_posting(db_session, dedupe_key="k2", name="Beta Inc", domain="beta.example")
    budget = Budget(db_session, load_profile(), p2.id)
    ctx2 = make_ctx(db_session, p2, search=WebSearch(SerperClient(HttpClient(), "K"), None, budget))
    res2 = PhoneStep().run(ctx2)
    assert res2.notes["how"] == "search_snippet" and res2.credits == {"websearch": 1.0}
    assert ctx2.phones == [("+1 514-555-0177", "switchboard", "search_snippet")]
    assert json.loads(serper.calls[0].request.content)["q"] == '"Beta Inc" contact phone'
    assert p2.company.main_phone == "+1 514-555-0177"


@respx.mock
def test_phone_none_found(db_session) -> None:
    for path in ("contact", "contact-us", "contactez-nous", "nous-joindre", ""):
        respx.get(f"https://acmelogistics.example/{path}").mock(return_value=httpx.Response(404))
    p = make_posting(db_session)
    res = PhoneStep().run(make_ctx(db_session, p))
    assert res.ok and res.notes["how"] == "none" and p.company.main_phone is None


def test_is_generic_email() -> None:
    assert is_generic_email("careers@acme.example") and is_generic_email("info@acme.example")
    assert is_generic_email("rh-recrutement@acme.example")
    assert not is_generic_email("dana.lee@acme.example")


def test_assemble_people_get_switchboard_and_rows_are_replaced(db_session) -> None:
    p = make_posting(db_session)
    db_session.add(Contact(posting_id=p.id, company_id=p.company_id, full_name="Old Row"))
    db_session.commit()
    ctx = make_ctx(db_session, p)
    ctx.phones = [("+1 514-555-0100", "switchboard", "serper_kg")]
    dana = Person(full_name="Dana Lee", title="Manager, Analytics", title_relevance=1.0,
                  email="dana.lee@acmelogistics.example", email_status="verified",
                  email_source="hunter_verifier", linkedin_url="https://ca.linkedin.com/in/dana",
                  has_email=True, has_direct_phone="Yes", sources=["apollo", "linkedin_search"])
    sam = Person(full_name="Sam Roy", title="Talent Acquisition Partner", title_relevance=0.6,
                 phone="+1 514-555-0101", phone_kind="direct", phone_source="posting")
    ana = Person(full_name="Ana P.", title="Data Analyst", title_relevance=0.3,
                 evidence={"last_name_obfuscated": True})
    extra = Person(full_name="Zed Four", title="Intern", title_relevance=0.3)
    ctx.people = [ana, extra, sam, dana]
    res = AssembleStep().run(ctx)
    assert res.ok and res.notes == {"rows": 3, "outcome": "people", "verified_email": True,
                                    "any_phone": True}
    rows = db_session.scalars(select(Contact).where(Contact.posting_id == p.id)
                              .order_by(Contact.confidence.desc())).all()
    assert [r.full_name for r in rows] == ["Dana Lee", "Sam Roy", "Ana P."]
    # the in-memory relationship must reflect the new rows without a re-query (CLI/dashboard)
    assert sorted(c.full_name for c in p.contacts) == ["Ana P.", "Dana Lee", "Sam Roy"]
    assert rows[0].confidence == 0.85 and rows[0].phone == "+1 514-555-0100"
    assert rows[0].phone_kind == "switchboard" and rows[0].email_status == "verified"
    assert rows[0].evidence["has_direct_phone_signal"] == "Yes"
    assert rows[0].evidence["sources"] == ["apollo", "linkedin_search"]
    assert rows[1].phone == "+1 514-555-0101" and rows[1].phone_kind == "direct"
    assert rows[1].confidence == round(0.95 * (0.6 + 0.4 * 0.6), 3)
    assert rows[2].phone_kind == "switchboard" and rows[2].email is None
    assert rows[2].email_status == "unverified" and rows[2].role_kind == "other"


def test_assemble_switchboard_only_and_generic_email(db_session) -> None:
    p = make_posting(db_session)
    ctx = make_ctx(db_session, p)
    ctx.phones = [("+1 514-555-0100", "switchboard", "https://acmelogistics.example/contact")]
    ctx.stated_emails = ["careers@acmelogistics.example"]
    res = AssembleStep().run(ctx)
    assert res.notes["outcome"] == "switchboard_only" and res.notes["rows"] == 1
    row = db_session.scalars(select(Contact).where(Contact.posting_id == p.id)).one()
    assert row.full_name is None and row.role_kind == "other" and row.confidence == 0.9
    assert (row.phone, row.phone_kind) == ("+1 514-555-0100", "switchboard")
    assert (row.email, row.email_status, row.email_source) == (
        "careers@acmelogistics.example", "verified", "posting"
    )
    assert row.evidence["switchboard_only"] is True and row.evidence["person_confidence"] == 0.0


def test_assemble_needs_manual_row(db_session) -> None:
    p = make_posting(db_session)
    ctx = make_ctx(db_session, p)
    ctx.titles = ["Manager, Analytics"]
    res = AssembleStep().run(ctx)
    assert res.notes["outcome"] == "needs_manual"
    row = db_session.scalars(select(Contact).where(Contact.posting_id == p.id)).one()
    assert row.confidence == 0.0 and row.role_kind == "other" and row.phone is None
    assert row.evidence["needs_manual"] is True and row.evidence["reason"] == "no person, no phone"
    assert row.evidence["website"] == "https://acmelogistics.example"
    assert row.evidence["titles"] == ["Manager, Analytics"]


def test_assemble_keeps_drafts_when_replacing_contacts(db_session) -> None:
    from jobfinder.db.models import Draft

    p = make_posting(db_session)
    old = Contact(posting_id=p.id, company_id=p.company_id, full_name="Old Row")
    p.contacts.append(old)
    db_session.flush()
    p.drafts.append(Draft(posting_id=p.id, contact_id=old.id, kind="email", body="hi"))
    db_session.commit()
    ctx = make_ctx(db_session, p)
    ctx.people = [Person(full_name="Dana Lee", title="Manager, Analytics", title_relevance=1.0)]
    res = AssembleStep().run(ctx)
    db_session.commit()
    assert res.ok and [c.full_name for c in p.contacts] == ["Dana Lee"]
    assert len(p.drafts) == 1 and p.drafts[0].contact_id is None
