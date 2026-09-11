from jobfinder import paths
from jobfinder.config import load_profile
from jobfinder.discovery.base import RawPosting, SearchProfile, filter_by_title


def _profile():
    return load_profile(paths.repo_root() / "tests" / "fixtures" / "profile.yaml")


def test_search_profile_from_default_profile() -> None:
    sp = SearchProfile.from_profile(_profile())
    assert sp.search_keywords[:2] == ["data analyst", "reporting analyst"]
    assert "bi developer" in sp.search_keywords
    keys = {q.key for q in sp.location_queries}
    assert {"canada-any", "remote-from-canada", "edmonton", "montreal", "vancouver", "uk"} == keys
    edm = next(q for q in sp.location_queries if q.key == "edmonton")
    assert (edm.country, edm.where, edm.remote_only) == ("CA", "Edmonton, AB", False)
    assert [q.key for q in sp.remote_queries] == ["remote-from-canada"]
    assert {q.key for q in sp.queries_for_country("GB")} == {"uk"}
    assert sp.country_name("GB") == "United Kingdom" and sp.max_days_old == 21


def test_filter_by_title_keeps_cluster_matches() -> None:
    raws = [
        RawPosting("x", "1", "u", "Senior Data Analyst", "A"),
        RawPosting("x", "2", "u", "Forklift Operator", "B"),
        RawPosting("x", "3", "u", "Analyst, Business Intelligence", "C"),
    ]
    kept = filter_by_title(raws, SearchProfile.from_profile(_profile()))
    assert [r.source_id for r in kept] == ["1", "3"]


def test_explicit_search_keywords_override_the_cluster_derivation() -> None:
    """Aggregators are only ever asked for the first two terms of each cluster; the rest only
    filter what comes back. An explicit list in the profile is sent verbatim (lower-cased,
    de-duplicated), and an empty list keeps the old derivation."""
    prof = _profile()
    prof.titles.search_keywords = ["HR Analyst", "workforce analyst", "hr analyst", "Data Analyst"]
    sp = SearchProfile.from_profile(prof)
    assert sp.search_keywords == ["hr analyst", "workforce analyst", "data analyst"]
    prof.titles.search_keywords = []
    derived = SearchProfile.from_profile(prof).search_keywords
    assert derived[:2] == ["data analyst", "reporting analyst"]
