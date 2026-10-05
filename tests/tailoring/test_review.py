from pathlib import Path

from jobfinder.llm.fake import FakeLLM
from jobfinder.tailoring.master_schema import MasterResume
from jobfinder.tailoring.models import TailoredOutput
from jobfinder.tailoring.review import review_documents, with_rules

FIXTURE = Path(__file__).parent.parent / "fixtures" / "master" / "resume.yaml"


def _draft(master: MasterResume) -> TailoredOutput:
    return TailoredOutput(resume=master.model_copy(deep=True), cover_letter="Draft letter.")


def test_review_revision_is_used() -> None:
    master = MasterResume.from_yaml(FIXTURE)
    revised = _draft(master).model_dump(mode="json")
    revised["cover_letter"] = "Sharper letter."
    llm = FakeLLM({"review_documents": {
        "score": 82, "critique": ["Lead with Power BI"], "revised": revised,
    }})
    out, rec = review_documents(
        _draft(master), master=master, master_cover="Own words.", posting_block="POSTING",
        lang="en", llm=llm,
    )
    assert out.cover_letter == "Sharper letter."
    assert rec == {"score": 82, "critique": ["Lead with Power BI"]}
    call = llm.calls[0]
    assert call["tier"] == "strong" and call["language"] == "en"
    assert "Draft letter." in call["user"] and "Own words." in call["user"]
    assert "POSTING" in call["user"] and "Never introduce an employer" in call["system"]


def test_review_failure_keeps_draft() -> None:
    master = MasterResume.from_yaml(FIXTURE)
    out, rec = review_documents(
        _draft(master), master=master, master_cover="", posting_block="P", lang="en",
        llm=FakeLLM({}),
    )
    assert out.cover_letter == "Draft letter." and "error" in rec


def test_review_invalid_revision_keeps_draft() -> None:
    master = MasterResume.from_yaml(FIXTURE)
    llm = FakeLLM({"review_documents": {"score": 70, "critique": [], "revised": {"x": 1}}})
    out, rec = review_documents(
        _draft(master), master=master, master_cover="", posting_block="P", lang="en", llm=llm,
    )
    assert out.cover_letter == "Draft letter." and "error" in rec


def test_rules_shared_by_both_prompts() -> None:
    rules = "Never introduce an employer"
    assert rules in with_rules("tailor_resume") and rules in with_rules("review_documents")
    assert rules not in with_rules("tailor_resume").split("\n\n", 1)[0]  # rules not duplicated
    assert "Relocation" in with_rules("tailor_resume")
    assert "280-320 words" in with_rules("tailor_resume")  # margin under the 350 hold


def test_draft_rationale_survives_review() -> None:
    master = MasterResume.from_yaml(FIXTURE)
    draft = _draft(master)
    draft.change_log, draft.gaps = ["Rewrote summary"], ["dbt"]
    revised = _draft(master).model_dump(mode="json")
    revised["change_log"], revised["gaps"] = ["Moved SQL first"], ["Snowflake", "dbt"]
    llm = FakeLLM({"review_documents": {"score": 80, "critique": ["c1"], "revised": revised}})
    out, _ = review_documents(
        draft, master=master, master_cover="", posting_block="POSTING\nTitle: x", lang="en",
        llm=llm,
    )
    assert out.change_log == ["Rewrote summary", "review: Moved SQL first"]
    assert out.gaps == ["dbt", "Snowflake"]
