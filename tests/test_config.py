from pathlib import Path

from jobfinder import paths
from jobfinder.config import load_profile, save_profile


def test_live_profile_parses() -> None:
    """The file Settings edits must always load; no value is pinned here on purpose. Absent on
    a fresh clone (gitignored) — then the example stands in for it."""
    live = paths.repo_root() / "config" / "profile.yaml"
    p = load_profile(live if live.exists() else paths.example_profile_path())
    assert p.titles.clusters and p.locations and p.scoring.match_threshold > 0


def test_default_profile_loads() -> None:
    p = load_profile(paths.repo_root() / "tests" / "fixtures" / "profile.yaml")
    assert "data analyst" in p.title_terms()
    keys = {loc.key for loc in p.enabled_locations()}
    assert {"canada-any", "remote-from-canada", "edmonton", "montreal", "vancouver", "uk"} <= keys
    assert p.scoring.match_threshold == 70
    assert p.scoring.max_hydrate_per_run == 100
    assert p.scoring.hydrate_delay_s == 5
    assert p.scoring.max_posting_age_days == 21
    assert "hr analyst" in p.title_terms() and "analyste rh" in p.title_terms()
    assert p.source_enabled("adzuna") is True
    assert p.source_daily_cap("jobbank_rss") == 40
    assert p.source_daily_cap("ats_boards") == 60      # pooled across every ats_* adapter
    assert p.source_daily_cap("remotive") is None      # uncapped adapters stay uncapped
    assert p.source_enabled("nonexistent") is True
    assert p.contacts.per_job_credit_cap["apollo"] == 3


def test_save_preserves_comments(tmp_path: Path) -> None:
    src = tmp_path / "profile.yaml"
    src.write_text(
        "titles:\n  clusters:\n    - {name: a, include: [x]}\n"
        "locations: []\n"
        "scoring:\n  match_threshold: 70   # keep me\n"
    )
    prof = load_profile(src)
    prof.scoring.match_threshold = 80
    save_profile(prof, src)
    text = src.read_text()
    assert "# keep me" in text
    assert "match_threshold: 80" in text
    assert load_profile(src).scoring.match_threshold == 80


def test_contacts_provider_limits_default() -> None:
    p = load_profile(paths.repo_root() / "tests" / "fixtures" / "profile.yaml")
    assert p.contacts.provider_limits["hunter"].monthly_units == 45
    assert p.contacts.provider_limits["hunter"].usd_per_unit == 0.0245
    assert p.contacts.provider_limits["apollo"].usd_per_unit is None
    assert p.contacts.provider_limits["serper"].paid is False
    assert p.contacts.max_runs_per_scan == 5 and p.contacts.search_fallback == "ddg"


def test_save_preserves_flow_style_and_skips_empty_defaults(tmp_path: Path) -> None:
    src = tmp_path / "profile.yaml"
    src.write_text(
        "titles:\n  clusters:\n    - {name: a, include: [x, y]}   # keep\n"
        "  exclude_words: [\"vp,\", intern]\n"
        "locations:\n  - {key: k1, enabled: true}\n"
        "scoring:\n  match_threshold: 70\n"
    )
    prof = load_profile(src)
    prof.titles.clusters[0].include.append("z")
    prof.locations[0].enabled = False
    save_profile(prof, src)
    text = src.read_text()
    assert "include: [x, y, z]" in text and "# keep" in text
    assert "exclude_words: [\"vp,\", intern]" in text
    assert "{key: k1, enabled: false}" in text  # flow map kept; no region/cities added
    assert "region" not in text and "cities" not in text and "daily_calls" not in text
    assert load_profile(src).locations[0].enabled is False


def test_example_profile_parses() -> None:
    """The tracked example must always load: a fresh clone starts from it."""
    p = load_profile(paths.example_profile_path())
    assert p.titles.clusters and p.locations and p.scoring.match_threshold > 0
    assert p.documents.file_name_pattern == "{kind}-{company}"


def test_missing_profile_is_created_from_the_example(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JOBFINDER_HOME", str(tmp_path))
    assert not paths.config_path().exists()
    p = load_profile()
    assert paths.config_path().exists()
    example = load_profile(paths.example_profile_path())
    assert p.scoring.match_threshold == example.scoring.match_threshold
    # save_profile needs a real file to round-trip comments into — it exists now
    p.scoring.match_threshold = 75
    save_profile(p)
    assert load_profile().scoring.match_threshold == 75
