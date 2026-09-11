import pytest

from jobfinder.discovery.normalize import (
    detect_ats,
    detect_language,
    normalize_company,
    normalize_title,
    parse_location,
    slugify,
)


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Senior Data Analyst (Remote)", ("data analyst", "senior")),
        ("Sr. BI Developer II - Contract", ("bi developer", "senior")),
        ("Analyste, intelligence d'affaires", ("analyste intelligence d affaires", "unspecified")),
        ("Lead Analytics Engineer", ("analytics engineer", "lead")),
        ("Data Analyst Intern", ("data analyst", "intern")),
        ("Director, Business Intelligence", ("business intelligence", "management")),
    ],
)
def test_normalize_title(title, expected) -> None:
    assert normalize_title(title) == expected


def test_normalize_company() -> None:
    assert normalize_company("Acme Logistics Inc.") == "acme logistics"
    assert normalize_company("ACME Logistics, Ltd") == "acme logistics"
    assert normalize_company("Société Générale S.A.") == "societe generale"


def test_parse_location_canada_city() -> None:
    loc = parse_location("Montréal, QC, Canada")
    assert (loc.country, loc.region, loc.city, loc.remote_type) == (
        "CA", "QC", "Montreal", "unknown",
    )


def test_parse_location_uk_and_remote() -> None:
    loc = parse_location("London, England", remote_hint="hybrid")
    assert (loc.country, loc.city, loc.remote_type, loc.remote_scope) == (
        "GB", "London", "hybrid", "uk",
    )
    loc2 = parse_location("Remote - Canada")
    assert (loc2.country, loc2.remote_type, loc2.remote_scope) == ("CA", "remote", "canada")
    loc3 = parse_location("Anywhere in the world", remote_hint="remote")
    assert (loc3.country, loc3.remote_scope) == (None, "worldwide")
    loc4 = parse_location("Edmonton, AB", country_hint="CA")
    assert (loc4.region, loc4.city) == ("AB", "Edmonton")


def test_detect_language() -> None:
    fr_text = "Nous recherchons un analyste pour les rapports et les données de vente."
    assert detect_language(fr_text) == "fr"
    en_text = "We are looking for an analyst to build the reports and the dashboards."
    assert detect_language(en_text) == "en"


def test_detect_ats() -> None:
    assert detect_ats("https://boards.greenhouse.io/acme/jobs/123") == ("greenhouse", "acme")
    assert detect_ats("https://job-boards.greenhouse.io/acme/jobs/123") == ("greenhouse", "acme")
    assert detect_ats("https://jobs.lever.co/acme/uuid") == ("lever", "acme")
    assert detect_ats("https://jobs.ashbyhq.com/acme/uuid") == ("ashby", "acme")
    assert detect_ats("https://apply.workable.com/acme/j/ABC/") == ("workable", "acme")
    assert detect_ats("https://jobs.smartrecruiters.com/Acme/123-title") == (
        "smartrecruiters", "Acme",
    )
    assert detect_ats("https://www.linkedin.com/jobs/view/1") is None


def test_slugify() -> None:
    assert slugify("Société Générale / BI Developer") == "societe-generale-bi-developer"
