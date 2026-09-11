from pathlib import Path

from jobfinder import paths
from jobfinder.config import load_profile

FIXTURE = Path(__file__).parent.parent / "fixtures" / "master" / "resume.yaml"


def test_settings_page_and_profile_save(client) -> None:
    r = client.get("/settings")
    assert r.status_code == 200 and "match_threshold" in r.text and "canada-any" in r.text
    assert "adzuna" in r.text
    form = {
        "titles_include": (
            "reporting-analytics:\ndata analyst\nreporting analyst\nbi-engineering:\nbi developer"
        ),
        "exclude_words": "director\nvp,\nintern", "seniority_allowed": ["senior", "lead"],
        "loc_canada-any": "on", "loc_uk": "on",
        "match_threshold": "75", "max_llm_scored_per_run": "20", "max_posting_age_days": "10",
        "min_keyword_overlap": "2", "candidate_note": " UK visa pending. ",
        "search_keywords": "hr analyst\nworkforce analyst\n",
        "src_adzuna": "on", "src_remotive": "on", "monthly_usd_cap": "40", "scan_run_at": "06:45",
        "digest_send_at": "07:15", "digest_enabled": "on", "public_base_url": "http://localhost:3838",
    }
    r = client.post("/settings/profile", data=form, follow_redirects=False)
    assert r.status_code == 303
    p = load_profile()
    assert p.scoring.match_threshold == 75 and p.titles.seniority_allowed == ["senior", "lead"]
    assert p.titles.exclude_words == ["director", "vp,", "intern"]
    assert [c.name for c in p.titles.clusters] == ["reporting-analytics", "bi-engineering"]
    assert "bi developer" in p.title_terms()
    assert {loc.key for loc in p.enabled_locations()} == {"canada-any", "uk"}
    assert p.source_enabled("adzuna") and not p.source_enabled("reed")
    assert p.contacts.monthly_usd_cap == 40
    assert p.scoring.candidate_note == "UK visa pending."
    assert p.titles.search_keywords == ["hr analyst", "workforce analyst"]
    assert p.scan.run_at == "06:45" and p.digest.send_at == "07:15"
    assert "# jobfinder target profile" in paths.config_path().read_text()


def test_master_editor_roundtrip(client) -> None:
    r = client.post(
        "/settings/master", data={"resume_yaml": FIXTURE.read_text(), "cover_letter": "Dear team,"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and (paths.master_dir() / "resume.yaml").exists()
    assert (paths.master_dir() / "cover-letter.md").read_text().startswith("Dear team,")
    r = client.post(
        "/settings/master", data={"resume_yaml": "contact: {}", "cover_letter": ""},
        follow_redirects=True,
    )
    assert "invalid" in r.text.lower()


def test_scan_now_redirects_to_runs(client, monkeypatch) -> None:
    import jobfinder.app.background as bg

    monkeypatch.setattr(bg, "start_scan_thread", lambda llm: None)
    r = client.post("/settings/scan", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/runs"


# --- seeding panel (Plan 7 follow-up) --------------------------------------------------------

def _seed_master_and_gaps(db_session=None) -> None:
    from sqlalchemy.orm import Session

    from jobfinder.db.models import Company, Posting, Score
    from jobfinder.db.session import get_engine

    text = FIXTURE.read_text().replace("Python, ", "")
    (paths.master_dir() / "resume.yaml").write_text(text)
    with Session(get_engine()) as s:
        co = Company(name="Acme", normalized_name="acme")
        p = Posting(
            company=co, title="Analyst", normalized_title="analyst", apply_url="u", dedupe_key="k",
            content_hash="h", status="match", description_text="x",
        )
        p.scores.append(Score(model="fake", fit_score=80, reasons=["r"],
                              missing_requirements=["Snowflake not shown"]))
        s.add(p)
        s.commit()


def test_seed_panel_lists_gaps(client) -> None:
    _seed_master_and_gaps()
    r = client.get("/settings")
    assert r.status_code == 200 and "Seed the master" in r.text and "snowflake" in r.text


def test_seed_propose_then_apply(client, fake_llm) -> None:
    from jobfinder.tailoring.master_schema import MasterResume

    _seed_master_and_gaps()
    master = MasterResume.from_yaml(paths.master_dir() / "resume.yaml")
    proposed = master.model_dump(mode="json")
    proposed["skills"][0]["items"].append("Snowflake")
    fake_llm.responses["seed_master"] = proposed
    r = client.post("/settings/seed", data={"terms": ["snowflake"], "notes": "used daily"})
    assert r.status_code == 200 and "+" in r.text and "Snowflake" in r.text and "Apply" in r.text
    assert "used daily" in fake_llm.calls[0]["user"]
    import re

    yaml_field = re.search(r'name="proposed_yaml">(.*?)</textarea>', r.text, re.S).group(1)
    import html

    r2 = client.post(
        "/settings/seed/apply",
        data={"proposed_yaml": html.unescape(yaml_field), "terms": ["snowflake"]},
        follow_redirects=False,
    )
    assert r2.status_code == 303 and "seeded" in r2.headers["location"]
    assert "Snowflake" in (paths.master_dir() / "resume.yaml").read_text()
    assert list(paths.master_dir().glob("resume.yaml.bak-*"))


def test_seed_apply_rejects_tampered_yaml(client) -> None:
    from jobfinder.tailoring.master_schema import MasterResume

    _seed_master_and_gaps()
    master = MasterResume.from_yaml(paths.master_dir() / "resume.yaml")
    bad = master.model_dump(mode="json")
    bad["skills"][0]["items"].append("Snowflake")
    bad["experience"][0]["bullets"].append("Cut costs by 30%.")
    from io import StringIO

    from ruamel.yaml import YAML

    buf = StringIO()
    YAML().dump(bad, buf)
    r = client.post(
        "/settings/seed/apply", data={"proposed_yaml": buf.getvalue(), "terms": ["snowflake"]},
        follow_redirects=True,
    )
    assert "rejected" in r.text.lower() and "30%" in r.text
    assert "Snowflake" not in (paths.master_dir() / "resume.yaml").read_text()
