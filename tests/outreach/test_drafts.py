from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from jobfinder.config import load_profile
from jobfinder.db.models import Company, Contact, Draft, Posting, Score
from jobfinder.llm.fake import FakeLLM
from jobfinder.outreach.drafts import draft_for_posting
from jobfinder.outreach.gmail_link import gmail_compose_url


def test_gmail_compose_url_encodes() -> None:
    url = gmail_compose_url(
        "dana@acme.example", "Re: Data Analyst role", "Hi Dana,\n\nLine two & more."
    )
    q = parse_qs(urlparse(url).query)
    assert urlparse(url).netloc == "mail.google.com" and q["view"] == ["cm"] and q["fs"] == ["1"]
    assert q["to"] == ["dana@acme.example"] and q["su"] == ["Re: Data Analyst role"]
    assert q["body"] == ["Hi Dana,\n\nLine two & more."]


def test_draft_for_posting_creates_email_and_script(home, db_session) -> None:
    co = Company(name="Acme", normalized_name="acme")
    p = Posting(
        company=co, title="Data Analyst", normalized_title="data analyst", apply_url="u",
        dedupe_key="k", content_hash="h", status="match", description_text="SQL Power BI",
        language="en",
    )
    p.scores.append(Score(model="fake", fit_score=80, reasons=["r"], one_line_summary="s"))
    c = Contact(
        company=co, full_name="Dana Lee", title="Manager, Analytics", role_kind="hiring_manager",
        email="dana@acme.example", confidence=0.8,
    )
    p.contacts.append(c)
    db_session.add(p)
    db_session.commit()
    (home / "voice.md").write_text("Direct, no fluff.")
    llm = FakeLLM({"outreach_drafts": {
        "email_subject": "Data Analyst application — JF", "email_body": "Hi Dana,\n\nShort note.",
        "call_script": "Hi, this is JF…",
    }})
    drafts = draft_for_posting(
        db_session, p.id, llm=llm, profile=load_profile(), voice_notes="Direct, no fluff."
    )
    kinds = {d.kind: d for d in drafts}
    assert kinds["email"].subject.startswith("Data Analyst") and kinds["email"].contact_id == c.id
    assert kinds["call_script"].body.startswith("Hi, this is JF")
    assert llm.calls[0]["tier"] == "strong" and "Dana Lee" in llm.calls[0]["user"]
    assert "Direct, no fluff." in llm.calls[0]["user"]
    draft_for_posting(db_session, p.id, llm=llm, profile=load_profile(), voice_notes="")
    assert len(db_session.scalars(select(Draft).where(Draft.posting_id == p.id)).all()) == 2
