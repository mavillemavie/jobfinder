from typer.testing import CliRunner

from jobfinder.cli import app
from jobfinder.config import load_profile
from jobfinder.db.models import Contact, ContactRun
from jobfinder.discovery.scan import run_scan
from tests.contacts.conftest import make_posting, make_settings

runner = CliRunner()


def test_run_scan_calls_contacts_hook_after_scoring(home, db_session) -> None:
    calls: list[str] = []
    run = run_scan(
        db_session, profile=load_profile(), settings=make_settings(), sources=[],
        scorer=lambda s: calls.append("score") or {"scored": 0},
        contacts=lambda s: calls.append("contacts") or {"runs": 0},
    )
    assert calls == ["score", "contacts"] and run.stats["contacts"] == {"runs": 0}
    run2 = run_scan(
        db_session, profile=load_profile(), settings=make_settings(), sources=[], no_llm=True,
        scorer=lambda s: {"scored": 0}, contacts=lambda s: {"runs": 9},
    )
    assert "contacts" not in run2.stats


def test_contacts_show_lists_rows(home, db_session) -> None:
    p = make_posting(db_session)
    db_session.add(Contact(posting_id=p.id, company_id=p.company_id, full_name="Dana Lee",
                           title="Manager, Analytics", role_kind="hiring_manager",
                           email="dana.lee@acmelogistics.example", email_status="verified",
                           phone="+1 514-555-0100", phone_kind="switchboard", confidence=0.85))
    db_session.commit()
    result = runner.invoke(app, ["contacts", str(p.id), "--show"])
    assert result.exit_code == 0, result.output
    assert "Dana Lee" in result.output and "verified" in result.output and "0.85" in result.output


def test_contacts_runs_waterfall(home, db_session, monkeypatch) -> None:
    p = make_posting(db_session)
    seen: dict = {}

    def fake_run(session, posting_id, *, llm, settings, profile, http=None, clients=None):
        seen["posting_id"] = posting_id
        session.add(Contact(posting_id=posting_id, company_id=p.company_id, full_name="Sam Roy",
                            role_kind="recruiter", confidence=0.3))
        run = ContactRun(posting_id=posting_id, status="ok", credits={"websearch": 2.0},
                         steps=[{"name": "assemble", "ok": True, "skipped": None, "error": None,
                                 "credits": {}, "notes": {"outcome": "people"}}])
        session.add(run)
        session.commit()
        return run

    monkeypatch.setattr("jobfinder.contacts.waterfall.run_for_posting", fake_run)
    result = runner.invoke(app, ["contacts", str(p.id)])
    assert result.exit_code == 0, result.output
    assert seen["posting_id"] == p.id and "Sam Roy" in result.output and "assemble" in result.output
    missing = runner.invoke(app, ["contacts", "999999"])
    assert missing.exit_code == 1 and "no posting" in missing.output
