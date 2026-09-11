from jobfinder.contacts.steps.posting_extract import PostingExtractStep, _clean_email
from jobfinder.contacts.steps.target_inference import TargetInferenceStep
from jobfinder.llm.fake import FakeLLM
from tests.contacts.conftest import make_ctx, make_posting

EXTRACT = {
    "people": [
        {"full_name": "Dana Lee", "title": "Manager, Analytics", "role_hint": "hiring_manager",
         "email": "Dana.Lee@AcmeLogistics.example", "phone": "514-555-0101"},
        {"full_name": "", "title": "", "role_hint": "unknown", "email": "", "phone": ""},
    ],
    "emails": ["careers@acmelogistics.example", "not-an-email"],
    "phones": ["(514) 555-0100"],
    "notes": "Contact block at the end of the posting.",
}
TITLES = {
    "titles": ["Gestionnaire, Analytique", "Manager, Analytics", "Directeur BI", "Director, BI",
               "manager, analytics"],
    "talent_title": "Partenaire, acquisition de talents",
    "language": "fr",
}


def test_clean_email() -> None:
    assert _clean_email(" Dana.Lee@Acme.Example ") == "dana.lee@acme.example"
    assert _clean_email("nope") is None and _clean_email("") is None and _clean_email(None) is None


def test_posting_extract_fills_people_emails_phones_and_ats(db_session) -> None:
    p = make_posting(
        db_session, apply_url="https://boards.greenhouse.io/acmelogistics/jobs/123",
        description="Questions? Call 514-555-0100 or 514 555 0199. Fax 2026-09-04.",
    )
    llm = FakeLLM({"contact_extract": EXTRACT})
    ctx = make_ctx(db_session, p, llm=llm)
    res = PostingExtractStep().run(ctx)
    assert res.ok and res.name == "posting_extract"
    assert llm.calls[0]["tier"] == "fast" and llm.calls[0]["task"] == "contact_extract"
    assert "Call 514-555-0100" in llm.calls[0]["user"]
    assert [x.full_name for x in ctx.people] == ["Dana Lee"]
    dana = ctx.people[0]
    assert dana.role_kind == "hiring_manager" and dana.title_relevance == 0.9
    assert (dana.email, dana.email_status, dana.email_source) == (
        "dana.lee@acmelogistics.example", "verified", "posting"
    )
    assert (dana.phone, dana.phone_kind, dana.phone_source) == (
        "+1 514-555-0101", "direct", "posting"
    )
    assert ctx.stated_emails == ["careers@acmelogistics.example"]
    assert ctx.phones == [
        ("+1 514-555-0100", "switchboard", "posting"),
        ("+1 514-555-0199", "switchboard", "posting"),
    ]
    assert ctx.has_verified_email() and ctx.has_any_phone()
    assert (ctx.company.ats_type, ctx.company.ats_board_token) == ("greenhouse", "acmelogistics")
    assert res.notes["ats"] == {"type": "greenhouse", "token": "acmelogistics"}


def test_posting_extract_with_nothing_stated(db_session) -> None:
    p = make_posting(db_session, description="We are hiring. Apply online.")
    empty = {"people": [], "emails": [], "phones": [], "notes": ""}
    ctx = make_ctx(db_session, p, llm=FakeLLM({"contact_extract": empty}))
    res = PostingExtractStep().run(ctx)
    assert res.ok and ctx.people == [] and ctx.phones == [] and ctx.stated_emails == []
    assert "ats" not in res.notes


def test_target_inference_dedupes_and_appends_talent_title(db_session) -> None:
    p = make_posting(db_session, language="fr", description="Poste bilingue.")
    llm = FakeLLM({"contact_titles": TITLES})
    ctx = make_ctx(db_session, p, llm=llm)
    res = TargetInferenceStep().run(ctx)
    assert res.ok and llm.calls[0]["language"] == "fr" and "LANGUAGE: fr" in llm.calls[0]["user"]
    assert ctx.titles == [
        "Gestionnaire, Analytique", "Manager, Analytics", "Directeur BI", "Director, BI",
        "Partenaire, acquisition de talents",
    ]
    assert res.notes["titles"] == ctx.titles
