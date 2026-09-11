from pathlib import Path

from jobfinder.llm.base import load_prompt, load_schema, validate_json
from jobfinder.scoring.resume_summary import build_resume_summary
from jobfinder.tailoring.master_schema import MasterResume

FIXTURE = Path(__file__).parent.parent / "fixtures" / "master" / "resume.yaml"


def test_summary_is_compact_and_complete() -> None:
    m = MasterResume.from_yaml(FIXTURE)
    s = build_resume_summary(m)
    assert "Reporting Analyst — Acme Logistics (2019-03 → Present)" in s
    assert "Power BI" in s and "B.Sc." in s and "French (native)" in s
    assert "jordan@example.com" not in s  # no contact details in LLM input
    assert len(s) < 2500


def test_score_schema_and_prompt_load() -> None:
    schema = load_schema("score")
    good = {
        "fit_score": 82,
        "reasons": ["a"],
        "missing_requirements": [],
        "seniority_match": "match",
        "eligibility": {
            "remote_from_canada": "yes",
            "uk_right_to_work_required": "no",
            "sponsorship_mentioned": "unclear",
        },
        "language": "en",
        "red_flags": [],
        "one_line_summary": "ok",
    }
    assert validate_json(good, schema) == []
    assert validate_json({**good, "fit_score": 101}, schema)
    assert "fit_score" in load_prompt("score_posting")
