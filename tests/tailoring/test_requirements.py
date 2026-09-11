from jobfinder.tailoring.requirements import (
    coverage_ratio,
    extract_requirement_terms,
    keyword_coverage,
)


def test_extract_terms_from_posting() -> None:
    desc = "Must have SQL, Power BI and Excel. Nice: dbt, Snowflake. Bilingual (French/English)."
    terms = extract_requirement_terms(desc, extra_terms={"reporting"})
    expected = {"sql", "power bi", "excel", "dbt", "snowflake", "french", "english", "bilingual"}
    assert expected <= set(terms)
    assert "reporting" not in terms  # extra term not present in text


def test_keyword_coverage_and_ratio() -> None:
    matched, missing = keyword_coverage(
        "I build Power BI dashboards with SQL.", ["sql", "power bi", "dbt"]
    )
    assert (matched, missing) == (["power bi", "sql"], ["dbt"])
    assert coverage_ratio(matched, missing) == 2 / 3
    assert coverage_ratio([], []) == 1.0
