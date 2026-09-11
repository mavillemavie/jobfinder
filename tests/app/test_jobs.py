import sys
import types
from pathlib import Path

from sqlalchemy.orm import Session

from jobfinder import paths
from jobfinder.db.models import Company, Contact, Document, Pipeline, Posting, Score
from jobfinder.db.session import get_engine
from jobfinder.tailoring.master_schema import MasterResume

FIXTURE = Path(__file__).parent.parent / "fixtures" / "master" / "resume.yaml"


def _seed() -> int:
    with Session(get_engine()) as s:
        co = Company(
            name="Acme Logistics", normalized_name="acme logistics", main_phone="+1 514 555 0100"
        )
        p = Posting(
            company=co, title="Senior Data Analyst", normalized_title="data analyst",
            apply_url="https://apply.test/1", dedupe_key="k", content_hash="h", status="match",
            description_text="Requirements: SQL, Power BI.",
        )
        p.scores.append(Score(
            model="fake", fit_score=88, reasons=["r1", "r2"], missing_requirements=["dbt"],
            eligibility={
                "remote_from_canada": "yes", "uk_right_to_work_required": "no",
                "sponsorship_mentioned": "no",
            },
            red_flags=[], one_line_summary="strong",
        ))
        p.pipeline = Pipeline(stage="new")
        p.contacts.append(Contact(
            company=co, full_name="Dana Lee", title="Manager, Analytics",
            role_kind="hiring_manager", email="dana@acme.example", email_status="verified",
            confidence=0.85,
            phone="+1 514 555 0101", phone_kind="direct",
        ))
        s.add(p)
        s.commit()
        return p.id


def test_job_page_shows_everything(client) -> None:
    pid = _seed()
    r = client.get(f"/jobs/{pid}")
    assert r.status_code == 200
    needles = (
        "Senior Data Analyst", "88", "r1", "dbt", "Dana Lee", "dana@acme.example",
        "+1 514 555 0101", "Generate docs", "Retry contact search", "https://apply.test/1",
    )
    for needle in needles:
        assert needle in r.text, needle


def test_tailor_action_renders_documents(client, fake_llm) -> None:
    (paths.master_dir() / "resume.yaml").write_text(FIXTURE.read_text())
    pid = _seed()
    master = MasterResume.from_yaml(FIXTURE)
    fake_llm.responses["tailor_resume"] = {
        "resume": master.model_dump(mode="json"), "cover_letter": "Dear team,\n\nHi.",
        "change_log": ["x"], "keyword_coverage": {"matched": ["sql"], "missing": []}, "gaps": [],
    }
    fake_llm.responses["truth_check"] = {"unsupported_claims": []}
    r = client.post(f"/jobs/{pid}/tailor")
    assert r.status_code == 200 and "Jordan-Test-Resume-AcmeLogistics.docx" in r.text
    assert "ready" in r.text
    with Session(get_engine()) as s:
        doc = s.query(Document).filter_by(posting_id=pid, format="docx", kind="resume").one()
    f = client.get(f"/jobs/{pid}/files/{doc.id}")
    assert f.status_code == 200
    assert f.headers["content-type"].startswith("application/vnd.openxmlformats")


def test_contacts_action_501_without_plan3_then_works_with_stub(client, monkeypatch) -> None:
    pid = _seed()
    monkeypatch.setitem(sys.modules, "jobfinder.contacts", None)
    monkeypatch.setitem(sys.modules, "jobfinder.contacts.waterfall", None)
    assert client.post(f"/jobs/{pid}/contacts").status_code == 501
    stub = types.ModuleType("jobfinder.contacts.waterfall")

    def run_for_posting(session, posting_id, *, llm, settings, profile):
        p = session.get(Posting, posting_id)
        p.contacts.append(Contact(
            company_id=p.company_id, full_name="Sam Roy", title="Recruiter",
            role_kind="recruiter", confidence=0.6,
        ))
        session.flush()
        return types.SimpleNamespace(status="ok", steps=[])

    stub.run_for_posting = run_for_posting
    monkeypatch.setitem(sys.modules, "jobfinder.contacts", types.ModuleType("jobfinder.contacts"))
    monkeypatch.setitem(sys.modules, "jobfinder.contacts.waterfall", stub)
    r = client.post(f"/jobs/{pid}/contacts")
    assert r.status_code == 200 and "Sam Roy" in r.text and "Dana Lee" in r.text


def test_stage_notes_activities(client) -> None:
    pid = _seed()
    r = client.post(f"/jobs/{pid}/stage", data={"stage": "applied"}, follow_redirects=False)
    assert r.status_code in (200, 303)
    r = client.post(f"/jobs/{pid}/notes", data={"notes": "Applied via portal"})
    assert r.status_code == 200
    r = client.post(
        f"/jobs/{pid}/activities",
        data={"kind": "call", "outcome": "voicemail", "body": "Left message"},
    )
    assert r.status_code == 200 and "voicemail" in r.text
    with Session(get_engine()) as s:
        p = s.get(Posting, pid)
        assert p.pipeline.stage == "contacted" and p.pipeline.applied_at is not None
        assert p.pipeline.notes == "Applied via portal"
        kinds = sorted(a.kind for a in p.activities)
        # applied, then auto-moved to contacted by the call
        assert kinds == ["call", "stage_change", "stage_change"]
    r = client.post(
        f"/jobs/{pid}/stage", data={"stage": "closed", "close_reason": "rejected"},
        follow_redirects=False,
    )
    with Session(get_engine()) as s:
        assert s.get(Posting, pid).pipeline.close_reason == "rejected"


def test_delete_contact_keeps_drafts(client) -> None:
    from jobfinder.db.models import Draft

    pid = _seed()
    with Session(get_engine()) as s:
        p = s.get(Posting, pid)
        cid = p.contacts[0].id
        p.drafts.append(Draft(posting_id=pid, contact_id=cid, kind="email", body="hi"))
        s.commit()
    r = client.post(f"/jobs/{pid}/contacts/{cid}/delete")
    assert r.status_code == 200 and "Dana Lee" not in r.text
    with Session(get_engine()) as s:
        p = s.get(Posting, pid)
        assert p.contacts == [] and len(p.drafts) == 1 and p.drafts[0].contact_id is None


def test_contacts_action_reports_failure_instead_of_500(client, monkeypatch) -> None:
    pid = _seed()
    stub = types.ModuleType("jobfinder.contacts.waterfall")

    def boom(session, posting_id, *, llm, settings, profile):
        raise RuntimeError("provider exploded")

    stub.run_for_posting = boom
    monkeypatch.setitem(sys.modules, "jobfinder.contacts.waterfall", stub)
    r = client.post(f"/jobs/{pid}/contacts")
    assert r.status_code == 200 and "provider exploded" in r.text and "Dana Lee" in r.text


def test_closing_as_rejected_dismisses_title_twins_and_leaves_the_inbox(client) -> None:
    pid = _seed()
    with Session(get_engine()) as s:
        p = s.get(Posting, pid)
        twin = Posting(
            company=p.company, title="Data Analyst (Toronto)", normalized_title="data analyst",
            apply_url="https://apply.test/2", dedupe_key="k2", content_hash="h", status="new",
            description_text="x", prefilter_result={"passed": True, "reason": "ok"},
        )
        s.add(twin)
        s.commit()
        twin_id = twin.id
    assert "Senior Data Analyst" in client.get("/").text
    r = client.post(
        f"/jobs/{pid}/stage", data={"stage": "closed", "close_reason": "rejected"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    with Session(get_engine()) as s:
        t = s.get(Posting, twin_id)
        assert t.status == "dismissed" and t.dismiss_reason == "rejected"
        assert t.prefilter_result["twin_of"] == pid
        assert s.get(Posting, pid).status == "match"  # its own row keeps its history
    # A match whose pipeline is closed is no longer an actionable inbox row.
    assert "Senior Data Analyst" not in client.get("/").text
    assert "Senior Data Analyst" in client.get("/?status=all").text
