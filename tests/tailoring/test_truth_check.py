from pathlib import Path

from jobfinder.llm.fake import FakeLLM
from jobfinder.tailoring.master_schema import Experience, MasterResume, SkillGroup
from jobfinder.tailoring.truth_check import deterministic_truth_check, run_truth_check

FIXTURE = Path(__file__).parent.parent / "fixtures" / "master" / "resume.yaml"


def test_faithful_tailoring_has_no_findings() -> None:
    master = MasterResume.from_yaml(FIXTURE)
    tailored = master.model_copy(deep=True)
    first = tailored.experience[0]
    first.bullets = [first.bullets[1], first.bullets[0]]
    tailored.summary = "Reporting analyst, 8 years, Power BI and SQL."
    assert deterministic_truth_check(tailored, master) == []


def test_injected_claims_are_caught() -> None:
    master = MasterResume.from_yaml(FIXTURE)
    tailored = master.model_copy(deep=True)
    tailored.experience.append(
        Experience(company="Gamma Corp", title="Head of Data", bullets=["Led 12 people."])
    )
    tailored.experience[0].title = "Director of Analytics"
    tailored.experience[0].bullets.append("Cut costs by 95% across 300 stores.")
    tailored.experience[1].bullets.append("Managed a team of 12.")  # sentence-final number
    tailored.skills.append(SkillGroup(category="Cloud", items=["Snowflake"]))
    tailored.certifications.append("AWS Solutions Architect")
    tailored.education[0].year = "2012"
    findings = deterministic_truth_check(tailored, master)
    joined = "\n".join(findings)
    needles = (
        "Gamma Corp", "Director of Analytics", "95%", "300", "Snowflake",
        "AWS Solutions Architect", "2012", "number not in master: 12",
    )
    for needle in needles:
        assert needle in joined, needle


def test_run_truth_check_combines_llm(home) -> None:
    master = MasterResume.from_yaml(FIXTURE)
    llm = FakeLLM({"truth_check": {"unsupported_claims": ["claims fluency in Spanish"]}})
    rep = run_truth_check(master, "I speak Spanish fluently.", master, llm)
    assert rep["deterministic"] == [] and rep["llm"] == ["claims fluency in Spanish"]
    assert rep["ok"] is False
    assert llm.calls[0]["tier"] == "strong"
