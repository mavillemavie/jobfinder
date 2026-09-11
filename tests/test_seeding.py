from datetime import UTC, datetime, timedelta
from pathlib import Path

from jobfinder.db.models import Company, Document, Posting, Score
from jobfinder.seeding import gap_report
from jobfinder.tailoring.master_schema import ContactInfo, MasterResume, SkillGroup

FIXTURE = Path(__file__).parent / "fixtures" / "master" / "resume.yaml"


def _small_master() -> MasterResume:
    return MasterResume(
        contact=ContactInfo(name="Jordan Test"),
        skills=[SkillGroup(category="Analytics", items=["SQL", "Power BI"])],
    )


def _seed(db_session) -> None:
    co = Company(name="Acme", normalized_name="acme")
    rows = []
    for i in range(3):
        p = Posting(
            company=co, title=f"Analyst {i}", normalized_title="analyst", apply_url="u",
            dedupe_key=f"k{i}", content_hash=f"h{i}", status="match", description_text="x",
        )
        rows.append(p)
    rows[0].scores.append(Score(
        model="fake", fit_score=80, reasons=["r"],
        missing_requirements=["Python not shown", "Statistics background not shown"],
    ))
    rows[1].scores.append(Score(
        model="fake", fit_score=75, reasons=["r"], missing_requirements=["Python for analytics"],
    ))
    rows[1].documents.append(Document(
        kind="resume", format="docx", path="/x.docx", status="needs_review", ats_score=70,
        ats_report={"keyword_missing": ["python", "snowflake"],
                    "gaps": ["Power Automate — not present in the master."]},
    ))
    rows[2].status = "prefiltered_out"
    rows[2].prefilter_result = {"passed": False, "reason": "low_overlap"}
    rows[2].description_text = "We want Snowflake and SQL experience."
    db_session.add_all(rows)
    db_session.commit()


def test_gap_report_ranks_missing_terms(home, db_session) -> None:
    _seed(db_session)
    master = _small_master()  # has SQL and Power BI, nothing else
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
    rep = gap_report(db_session, master, since=since)
    by = {r["term"]: r for r in rep}
    assert [r["term"] for r in rep[:2]] == ["python", "snowflake"]
    assert by["python"] == {"term": "python", "count": 3, "scores": 2, "documents": 1, "drops": 0}
    assert by["snowflake"] == {
        "term": "snowflake", "count": 2, "scores": 0, "documents": 1, "drops": 1,
    }
    assert by["statistics"]["count"] == 1 and by["power automate"]["count"] == 1
    assert "sql" not in by


# --- Task 2: seed proposal with truth guard -------------------------------------------------

from jobfinder.llm.fake import FakeLLM  # noqa: E402
from jobfinder.seeding import SeedRejected, master_diff, propose_seed, seed_guard  # noqa: E402


def _with_python(master: MasterResume) -> dict:
    data = master.model_dump(mode="json")
    data["experience"][1]["bullets"][0] += " Also built reusable Python scripts for the reports."
    data["skills"][1]["items"].append("Python")
    return data


def test_propose_seed_accepts_a_faithful_weave_in(home) -> None:
    master = MasterResume.from_yaml(FIXTURE)
    master.skills[1].items.remove("Python")  # fixture already has it; start without
    proposed = _with_python(master)
    llm = FakeLLM({"seed_master": proposed})
    new, changes = propose_seed(master, ["Python"], llm)
    assert "Python" in new.skills[1].items and "Python scripts" in new.experience[1].bullets[0]
    assert any("Beta Retail" in c for c in changes) and any("skills" in c.lower() for c in changes)
    assert llm.calls[0]["tier"] == "strong" and "Python" in llm.calls[0]["user"]
    llm2 = FakeLLM({"seed_master": proposed})
    propose_seed(master, ["Python"], llm2, notes="Python: working knowledge only")
    assert "PLACEMENT NOTES" in llm2.calls[0]["user"]
    assert "working knowledge" in llm2.calls[0]["user"]
    assert "+" in master_diff(master, new) and "Python" in master_diff(master, new)


def test_propose_seed_rejects_new_claims_and_missing_terms(home) -> None:
    master = MasterResume.from_yaml(FIXTURE)
    master.skills[1].items.remove("Python")
    bad = _with_python(master)
    bad["experience"][0]["bullets"].append("Cut costs by 30% across 12 sites.")
    try:
        propose_seed(master, ["Python"], FakeLLM({"seed_master": bad}))
    except SeedRejected as exc:
        assert "30%" in str(exc) or "12" in str(exc)
    else:
        raise AssertionError("fabricated metric was accepted")
    untouched = master.model_dump(mode="json")  # the fixture's bullets already say Python,
    try:  # so use a term it genuinely lacks
        propose_seed(master, ["Snowflake"], FakeLLM({"seed_master": untouched}))
    except SeedRejected as exc:
        assert "term not placed: snowflake" in str(exc)
    else:
        raise AssertionError("missing term was accepted")
    shrunk = _with_python(master)
    shrunk["experience"].pop()
    reasons = seed_guard(master, MasterResume.model_validate(shrunk), ["Python"])
    assert any("removed" in r for r in reasons)


def test_seed_cli_diff_then_apply(home, monkeypatch) -> None:
    from typer.testing import CliRunner

    from jobfinder import paths
    from jobfinder.cli import app

    text = FIXTURE.read_text().replace("Python, ", "")
    (paths.master_dir() / "resume.yaml").write_text(text)
    master = MasterResume.from_yaml(paths.master_dir() / "resume.yaml")
    fake = FakeLLM({"seed_master": _with_python(master)})
    monkeypatch.setattr("jobfinder.llm.get_llm", lambda settings, **kw: fake)
    r = CliRunner().invoke(app, ["seed", "--term", "Python"])
    assert r.exit_code == 0, r.output
    assert "+" in r.stdout and "Python" in r.stdout and "not applied" in r.stdout
    assert "Python" not in (paths.master_dir() / "resume.yaml").read_text()
    r = CliRunner().invoke(app, ["seed", "--term", "Python", "--apply"])
    assert r.exit_code == 0, r.output
    assert "Python" in (paths.master_dir() / "resume.yaml").read_text()
    assert list(paths.master_dir().glob("resume.yaml.bak-*"))


def test_seed_guard_accepts_inflected_terms() -> None:
    master = MasterResume.from_yaml(FIXTURE)
    new = master.model_copy(deep=True)
    new.experience[0].bullets[0] += " Presented results to stakeholders."
    new.skills[0].items.append("Stakeholder communication")
    assert seed_guard(master, new, ["stakeholder"]) == []
    assert "term not placed: snowflake" in seed_guard(master, new, ["snowflake"])
