import httpx
import pytest
import respx
from sqlalchemy import select

from jobfinder.db.models import Company, Posting
from jobfinder.discovery.fetch import BudgetExceeded, HttpClient, RateLimited
from jobfinder.discovery.hydrate import MAX_HYDRATE_ATTEMPTS, _hydrate_url, hydrate_posting


def _seed(db_session, url: str) -> Posting:
    co = Company(name="Acme", normalized_name="acme")
    p = Posting(
        company=co,
        title="Data Analyst",
        normalized_title="data analyst",
        apply_url=url,
        dedupe_key="k",
        content_hash="h",
        description_text="short",
        description_complete=False,
    )
    db_session.add(p)
    db_session.commit()
    return p


@respx.mock
def test_hydrate_replaces_description_and_detects_ats(db_session) -> None:
    respx.get("https://agg.test/go").mock(
        return_value=httpx.Response(302, headers={"Location": "https://boards.greenhouse.io/acme/jobs/7"})
    )
    snippet = "Nous cherchons un analyste pour les rapports et les données. " * 5
    respx.get("https://boards.greenhouse.io/acme/jobs/7").mock(
        return_value=httpx.Response(
            200,
            text=(
                "<html><body><main><h1>Data Analyst</h1><p>"
                + snippet
                + "</p></main></body></html>"
            ),
        )
    )
    p = _seed(db_session, "https://agg.test/go")
    assert hydrate_posting(db_session, p, HttpClient()) is True
    assert p.description_complete and "analyste" in p.description_text and p.language == "fr"
    assert db_session.scalar(select(Company)).ats_type == "greenhouse"


@respx.mock
def test_hydrate_failure_keeps_snippet(db_session) -> None:
    respx.get("https://agg.test/dead").mock(return_value=httpx.Response(404))
    p = _seed(db_session, "https://agg.test/dead")
    assert hydrate_posting(db_session, p, HttpClient(retries=0)) is False
    assert p.description_text == "short" and p.description_complete is False
    assert p.prefilter_result == {"hydrate_error": "404", "hydrate_attempts": 1}


@respx.mock
def test_budget_exceeded_propagates(db_session) -> None:
    respx.get("https://agg.test/b").mock(
        return_value=httpx.Response(200, text="<main>short text here</main>")
    )
    p = _seed(db_session, "https://agg.test/b")
    client = HttpClient()
    client.set_call_budget(0)
    with pytest.raises(BudgetExceeded):
        hydrate_posting(db_session, p, client)
    assert p.description_complete is False
    assert p.prefilter_result is None


@respx.mock
def test_rate_limited_propagates(db_session) -> None:
    respx.get("https://agg.test/rl").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "60"})
    )
    p = _seed(db_session, "https://agg.test/rl")
    with pytest.raises(RateLimited):
        hydrate_posting(db_session, p, HttpClient(retries=0))


@respx.mock
def test_retry_exhausted_records_error_class(db_session) -> None:
    respx.get("https://agg.test/500").mock(return_value=httpx.Response(500))
    p = _seed(db_session, "https://agg.test/500")
    assert hydrate_posting(db_session, p, HttpClient(retries=0)) is False
    assert p.prefilter_result["hydrate_error"] == "SourceError"


@respx.mock
def test_not_longer_fetch_marks_complete_with_note(db_session) -> None:
    co = Company(name="Acme", normalized_name="acme")
    p = Posting(
        company=co,
        title="Data Analyst",
        normalized_title="data analyst",
        apply_url="https://agg.test/c",
        dedupe_key="k",
        content_hash="h",
        description_text="x" * 200,
        description_complete=False,
    )
    db_session.add(p)
    db_session.commit()
    respx.get("https://agg.test/c").mock(
        return_value=httpx.Response(200, text="<main>hi</main>")
    )
    assert hydrate_posting(db_session, p, HttpClient()) is False
    assert p.description_complete is True
    assert p.description_text == "x" * 200
    assert p.prefilter_result["hydrate_note"] == "not_longer"


def test_hydrate_url_uses_linkedin_guest_endpoint() -> None:
    co = Company(name="Acme", normalized_name="acme")
    linkedin_posting = Posting(
        company=co,
        title="Data Analyst",
        normalized_title="data analyst",
        apply_url="https://www.linkedin.com/jobs/view/data-analyst-at-acme-4123456789",
        dedupe_key="k1",
        content_hash="h",
        description_text="short",
        description_complete=False,
    )
    assert _hydrate_url(linkedin_posting) == (
        "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4123456789"
    )

    other_posting = Posting(
        company=co,
        title="Data Analyst",
        normalized_title="data analyst",
        apply_url="https://agg.test/go",
        dedupe_key="k2",
        content_hash="h",
        description_text="short",
        description_complete=False,
    )
    assert _hydrate_url(other_posting) == "https://agg.test/go"


@respx.mock
def test_gives_up_after_three_failed_attempts(db_session) -> None:
    """A permanently broken apply_url must stop eating a hydration slot every run."""
    route = respx.get("https://agg.test/dead").mock(return_value=httpx.Response(404))
    p = _seed(db_session, "https://agg.test/dead")
    client = HttpClient(retries=0)

    for attempt in range(1, MAX_HYDRATE_ATTEMPTS):
        assert hydrate_posting(db_session, p, client) is False
        assert p.description_complete is False
        assert p.prefilter_result["hydrate_attempts"] == attempt
        assert "hydrate_note" not in p.prefilter_result

    assert hydrate_posting(db_session, p, client) is False
    assert p.prefilter_result["hydrate_attempts"] == MAX_HYDRATE_ATTEMPTS
    assert p.prefilter_result["hydrate_note"] == "gave_up"
    # marked complete so the prefilter scores it on the snippet it already has
    assert p.description_complete is True and p.description_text == "short"

    # and a further call is a no-op — no fourth request
    assert hydrate_posting(db_session, p, client) is False
    assert route.call_count == MAX_HYDRATE_ATTEMPTS


@respx.mock
def test_success_after_a_failure_clears_the_hydrate_bookkeeping(db_session) -> None:
    route = respx.get("https://agg.test/flappy").mock(
        side_effect=[
            httpx.Response(404),
            httpx.Response(200, text="<main>" + ("A full description. " * 10) + "</main>"),
        ]
    )
    p = _seed(db_session, "https://agg.test/flappy")
    client = HttpClient(retries=0)
    assert hydrate_posting(db_session, p, client) is False
    assert p.prefilter_result["hydrate_attempts"] == 1

    assert hydrate_posting(db_session, p, client) is True
    assert route.call_count == 2
    assert "hydrate_error" not in (p.prefilter_result or {})
    assert "hydrate_attempts" not in (p.prefilter_result or {})
