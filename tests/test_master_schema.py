from pathlib import Path

from jobfinder import paths
from jobfinder.tailoring.master_schema import MasterResume, load_master, master_exists

FIXTURE = Path(__file__).parent / "fixtures" / "master" / "resume.yaml"


def test_roundtrip_and_skill_terms(tmp_path: Path) -> None:
    m = MasterResume.from_yaml(FIXTURE)
    assert m.contact.name == "Jordan Test"
    assert len(m.experience) == 2
    terms = m.skill_terms()
    assert {"sql", "power bi", "python", "kpi"} <= terms
    out = tmp_path / "r.yaml"
    m.to_yaml(out)
    assert MasterResume.from_yaml(out) == m


def test_load_master_uses_home(home: Path) -> None:
    assert master_exists() is False
    (paths.master_dir() / "resume.yaml").write_text(FIXTURE.read_text())
    assert master_exists() is True
    assert load_master().contact.name == "Jordan Test"


def test_overlap_terms_splits_phrases_into_short_terms() -> None:
    from jobfinder.tailoring.master_schema import ContactInfo, SkillGroup

    m = MasterResume(contact=ContactInfo(name="x"), skills=[SkillGroup(category="c", items=[
        "advanced excel (pivot tables, power pivot, power query)",
        "created data dashboards in power bi and tableau with sql and dax queries.",
        "confident, curious, and a strong sense of initiative to optimize efficiencies.",
        "sql",
    ])])
    t = m.overlap_terms()
    assert {"sql", "advanced excel", "pivot tables", "power pivot", "power query", "power bi",
            "tableau", "dax queries"} <= t
    assert "created data dashboards in power bi and tableau with sql and dax queries." in t
    assert not any(len(x.split()) > 4 for x in t if x not in m.skill_terms())
