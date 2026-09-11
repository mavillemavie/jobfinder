from jobfinder import paths
from jobfinder.config import load_profile
from jobfinder.db.models import Company, Posting
from jobfinder.discovery.base import RawPosting, SearchProfile, filter_by_title
from jobfinder.discovery.normalize import normalize_title
from jobfinder.discovery.prefilter import keyword_overlap, prefilter, title_matches

SKILLS = {"sql", "power bi", "python", "tableau", "dax", "etl"}


def _profile():
    return load_profile(paths.repo_root() / "tests" / "fixtures" / "profile.yaml")


def _posting(**kw) -> Posting:
    base = dict(
        company=Company(name="Acme", normalized_name="acme"), title="Data Analyst",
        normalized_title="data analyst", seniority="unspecified", country="CA", region="QC",
        city="Montreal", remote_type="unknown", remote_scope=None, description_complete=True,
        description_text="SQL, Power BI and Python for ETL pipelines.", apply_url="u",
        dedupe_key="k", content_hash="h",
    )
    base.update(kw)
    return Posting(**base)


def test_title_matches_token_subset() -> None:
    terms = _profile().title_terms()
    assert title_matches("data analyst finance", terms) == "data analyst"
    assert title_matches("analyst business intelligence", terms) == "business intelligence analyst"
    assert title_matches("forklift operator", terms) is None


def test_french_titles_match_the_accented_cluster_terms() -> None:
    """Quebec postings are titled in French; the cluster list carries the accented terms
    while normalize_title() ASCII-folds every stored title, so matching must fold too."""
    terms = _profile().title_terms()
    assert title_matches(normalize_title("Analyste de données")[0], terms) is not None
    assert title_matches(normalize_title("Analyste données sénior")[0], terms) is not None
    assert title_matches(normalize_title("Analyste culinaire")[0], terms) is None


def test_french_posting_passes_the_full_prefilter() -> None:
    fr = _posting(
        title="Analyste de données",
        normalized_title=normalize_title("Analyste de données")[0],
        description_text="Vous utiliserez SQL, Power BI et Python pour nos pipelines ETL.",
        language="fr",
    )
    r = prefilter(fr, _profile(), SKILLS)
    assert r.passed and r.reason == "ok" and r.location_key == "montreal"


def test_keyword_overlap_word_boundaries() -> None:
    assert keyword_overlap("We use SQL and Power BI daily; no pythonic tricks.", SKILLS) == 2


def test_prefilter_pass() -> None:
    r = prefilter(_posting(), _profile(), SKILLS)
    assert r.passed and r.reason == "ok" and r.location_key == "montreal" and r.overlap == 4


def test_prefilter_reasons() -> None:
    p = _profile()
    p.titles.seniority_allowed = ["junior", "intermediate", "senior", "lead", "unspecified"]
    forklift = _posting(normalized_title="forklift operator")
    assert prefilter(forklift, p, SKILLS).reason == "wrong_title"
    director = _posting(title="Director, Data Analyst", seniority="management")
    assert prefilter(director, p, SKILLS).reason == "excluded_word"
    assert prefilter(_posting(seniority="management"), p, SKILLS).reason == "seniority"
    assert prefilter(_posting(country="US", city="Austin"), p, SKILLS).reason == "wrong_location"
    unrelated = _posting(description_text="nothing relevant")
    assert prefilter(unrelated, p, SKILLS).reason == "low_overlap"
    thin = _posting(description_text="nothing")
    assert prefilter(thin, p, SKILLS, check_overlap=False).passed


def test_prefilter_remote_from_canada_and_uk() -> None:
    p = _profile()
    worldwide = _posting(country=None, city=None, remote_type="remote", remote_scope="worldwide")
    r = prefilter(worldwide, p, SKILLS)
    assert r.passed and r.location_key == "remote-from-canada"
    r2 = prefilter(_posting(country="GB", region=None, city="Leeds"), p, SKILLS)
    assert r2.passed and r2.location_key == "uk"
    us_remote = _posting(country="US", city=None, remote_type="remote", remote_scope="us")
    r3 = prefilter(us_remote, p, SKILLS)
    assert r3.reason == "wrong_location"


def test_filter_by_title_from_task1() -> None:
    raws = [
        RawPosting("x", "1", "u", "Senior Data Analyst", "A"),
        RawPosting("x", "2", "u", "Chef", "B"),
    ]
    kept = filter_by_title(raws, SearchProfile.from_profile(_profile()))
    assert [r.source_id for r in kept] == ["1"]
