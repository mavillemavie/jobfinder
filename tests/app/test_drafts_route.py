from sqlalchemy.orm import Session

from jobfinder.db.models import Company, Contact, Pipeline, Posting, Score
from jobfinder.db.session import get_engine


def _seed() -> int:
    with Session(get_engine()) as s:
        co = Company(name="Acme", normalized_name="acme")
        p = Posting(
            company=co, title="Data Analyst", normalized_title="data analyst", apply_url="u",
            dedupe_key="k", content_hash="h", status="match", description_text="SQL",
        )
        p.scores.append(Score(model="fake", fit_score=80, reasons=["r"], one_line_summary="s"))
        p.pipeline = Pipeline(stage="new")
        p.contacts.append(Contact(
            company=co, full_name="Dana Lee", email="dana@acme.example",
            role_kind="hiring_manager", confidence=0.8,
        ))
        s.add(p)
        s.commit()
        return p.id


def test_drafts_route_and_gmail_link(client, fake_llm) -> None:
    pid = _seed()
    fake_llm.responses["outreach_drafts"] = {
        "email_subject": "Subj", "email_body": "Body", "call_script": "Script",
    }
    r = client.post(f"/jobs/{pid}/drafts")
    assert r.status_code == 200 and "mail.google.com/mail/?view=cm" in r.text and "Script" in r.text
    r = client.get(f"/jobs/{pid}")
    assert "Compose in Gmail" in r.text
