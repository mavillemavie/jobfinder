from __future__ import annotations

import re
from collections.abc import Iterable

VOCABULARY: set[str] = {
    # query / BI / viz
    "sql", "t-sql", "pl/sql", "mysql", "postgresql", "sql server", "oracle", "snowflake",
    "bigquery", "redshift", "databricks", "spark", "power bi", "dax", "power query", "tableau",
    "looker", "qlik", "qlikview", "qlik sense", "ssrs", "ssis", "ssas", "cognos", "microstrategy",
    "metabase", "excel", "vba", "google sheets", "google analytics",
    # data eng / cloud
    "etl", "elt", "dbt", "airflow", "azure", "aws", "gcp", "data warehouse", "data lake",
    "data modeling", "data modelling", "dimensional modeling", "star schema", "data pipeline",
    "data quality", "data governance", "api", "rest", "json", "git", "github", "gitlab", "docker",
    "linux",
    # languages / stats / ml
    "python", "pandas", "numpy", "r", "sas", "spss", "matlab", "scala", "java", "javascript",
    "typescript", "statistics", "statistical analysis", "forecasting", "regression",
    "a/b testing", "machine learning", "predictive modeling", "predictive modelling",
    "data mining", "time series",
    # Microsoft / HR-tech ecosystem (frequent in JF's target postings)
    "power automate", "power platform", "power apps", "sharepoint", "microsoft fabric", "workday",
    "successfactors", "peoplesoft", "hris", "people analytics", "workforce analytics",
    "variance analysis", "financial modeling", "financial modelling", "budgeting", "six sigma",
    # business / process
    "kpi", "kpis", "dashboard", "dashboards", "reporting", "report automation",
    "requirements gathering", "stakeholder", "stakeholders", "agile", "scrum", "jira",
    "confluence", "salesforce", "sap", "erp", "crm", "finance", "accounting", "supply chain",
    "logistics", "healthcare", "insurance", "banking", "retail", "e-commerce",
    "marketing analytics", "operations", "ad hoc analysis", "data visualization",
    "data visualisation", "storytelling", "presentation", "communication", "problem solving",
    # language
    "french", "english", "bilingual", "français", "anglais", "bilingue",
}


def _present(term: str, low: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", low) is not None


def extract_requirement_terms(description: str, extra_terms: Iterable[str] = ()) -> list[str]:
    low = description.lower()
    vocab = VOCABULARY | {t.lower().strip() for t in extra_terms if t.strip()}
    return sorted(t for t in vocab if len(t) > 1 and _present(t, low))


def keyword_coverage(text: str, terms: Iterable[str]) -> tuple[list[str], list[str]]:
    low = text.lower()
    matched, missing = [], []
    for term in sorted({t.lower() for t in terms}):
        (matched if _present(term, low) else missing).append(term)
    return matched, missing


def coverage_ratio(matched: list[str], missing: list[str]) -> float:
    total = len(matched) + len(missing)
    return 1.0 if total == 0 else len(matched) / total
