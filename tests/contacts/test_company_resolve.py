import json
from pathlib import Path

import httpx
import respx

from jobfinder.config import load_profile
from jobfinder.contacts.budget import Budget
from jobfinder.contacts.providers.search import DuckDuckGoClient, SerperClient, WebSearch
from jobfinder.contacts.steps.company_resolve import (
    CompanyResolveStep,
    domain_from_apply_url,
    registrable_domain,
)
from jobfinder.discovery.fetch import HttpClient
from tests.contacts.conftest import make_ctx, make_posting

FIX = Path(__file__).parent.parent / "fixtures" / "contacts"


def test_registrable_domain_and_apply_url() -> None:
    assert registrable_domain("careers.acme.com") == "acme.com"
    assert registrable_domain("jobs.acme.co.uk:443") == "acme.co.uk"
    assert registrable_domain("localhost") is None and registrable_domain("10.0.0.1") is None
    assert domain_from_apply_url("https://careers.acme.com/jobs/1") == "acme.com"
    assert domain_from_apply_url("https://boards.greenhouse.io/acme/jobs/1") is None
    assert domain_from_apply_url("https://ca.linkedin.com/jobs/view/1") is None
    assert domain_from_apply_url("https://www.jobbank.gc.ca/jobsearch/jobposting/1") is None
    assert domain_from_apply_url("") is None


def test_cached_domain_costs_nothing(db_session) -> None:
    p = make_posting(db_session)  # company.domain preset by the helper
    ctx = make_ctx(db_session, p)
    ctx.domain = ctx.website = None
    res = CompanyResolveStep().run(ctx)
    assert res.ok and res.notes["how"] == "cached" and res.credits == {}
    assert ctx.domain == "acmelogistics.example" and ctx.website == "https://acmelogistics.example"


def test_domain_from_apply_url_without_search(db_session) -> None:
    p = make_posting(db_session, domain=None, apply_url="https://careers.acmelogistics.example/j/1")
    ctx = make_ctx(db_session, p)
    res = CompanyResolveStep().run(ctx)
    assert res.notes["how"] == "apply_url" and ctx.domain == "acmelogistics.example"
    assert p.company.domain == "acmelogistics.example"
    assert p.company.website == "https://acmelogistics.example"


@respx.mock
def test_search_uses_knowledge_graph_and_keeps_it(db_session) -> None:
    respx.post("https://google.serper.dev/search").mock(
        return_value=httpx.Response(200, json=json.loads((FIX / "serper_search.json").read_text()))
    )
    p = make_posting(db_session, domain=None, apply_url="https://boards.greenhouse.io/acme/jobs/1")
    budget = Budget(db_session, load_profile(), posting_id=p.id)
    ws = WebSearch(SerperClient(HttpClient(), "K"), None, budget)
    ctx = make_ctx(db_session, p, search=ws)
    res = CompanyResolveStep().run(ctx)
    assert res.ok and res.notes["how"] == "knowledge_graph" and res.credits == {"websearch": 1.0}
    assert ctx.domain == "acmelogistics.example"
    assert ctx.knowledge_graph["attributes"]["Phone"] == "+1 514-555-0100"
    assert budget.job_units["websearch"] == 1.0


@respx.mock
def test_search_matches_title_when_no_knowledge_graph(db_session) -> None:
    respx.get(url__regex=r"https://html\.duckduckgo\.com/html/.*").mock(
        return_value=httpx.Response(200, text=(FIX / "ddg.html").read_text())
    )
    p = make_posting(db_session, domain=None, apply_url="https://boards.greenhouse.io/acme/jobs/1")
    ws = WebSearch(None, DuckDuckGoClient(HttpClient()), Budget(db_session, load_profile(), p.id))
    ctx = make_ctx(db_session, p, search=ws)
    res = CompanyResolveStep().run(ctx)
    # first DDG hit is linkedin.com (a job host, skipped); the second's title names the company
    assert res.notes["how"] == "search" and ctx.domain == "acmelogistics.example"


@respx.mock
def test_no_match_and_no_backend(db_session) -> None:
    respx.post("https://google.serper.dev/search").mock(
        return_value=httpx.Response(200, json={"organic": [
            {"title": "Unrelated", "link": "https://other.example/", "snippet": ""}]})
    )
    p = make_posting(db_session, domain=None, apply_url="https://boards.greenhouse.io/acme/jobs/1")
    ws = WebSearch(SerperClient(HttpClient(), "K"), None, Budget(db_session, load_profile(), p.id))
    res = CompanyResolveStep().run(make_ctx(db_session, p, search=ws))
    assert res.ok is False and res.error == "no matching result"
    assert res.credits == {"websearch": 1.0}
    res2 = CompanyResolveStep().run(make_ctx(db_session, p, search=None))
    assert res2.skipped == "no search backend"
