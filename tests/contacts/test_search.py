import json
from pathlib import Path

import httpx
import pytest
import respx

from jobfinder.config import load_profile
from jobfinder.contacts.budget import Budget
from jobfinder.contacts.providers.search import DuckDuckGoClient, SerperClient, WebSearch
from jobfinder.discovery.fetch import HttpClient

FIX = Path(__file__).parent.parent / "fixtures" / "contacts"


@respx.mock
def test_serper_search_parses_organic_and_knowledge_graph() -> None:
    route = respx.post("https://google.serper.dev/search").mock(
        return_value=httpx.Response(200, json=json.loads((FIX / "serper_search.json").read_text()))
    )
    res = SerperClient(HttpClient(), "K").search('"Acme Logistics" official site', gl="ca", hl="fr")
    req = route.calls[0].request
    assert req.headers["X-API-KEY"] == "K"
    body = json.loads(req.content)
    assert body["q"].startswith('"Acme') and body["gl"] == "ca" and body["hl"] == "fr"
    assert body["num"] == 10
    assert res.backend == "serper" and len(res.hits) == 2
    assert res.hits[0].link == "https://acmelogistics.example/" and "3PL" in res.hits[0].snippet
    assert res.knowledge_graph["attributes"]["Phone"] == "+1 514-555-0100"


@respx.mock
def test_ddg_parses_results_and_unwraps_redirects() -> None:
    respx.get(url__regex=r"https://html\.duckduckgo\.com/html/.*").mock(
        return_value=httpx.Response(200, text=(FIX / "ddg.html").read_text())
    )
    res = DuckDuckGoClient(HttpClient()).search('site:linkedin.com/in "Acme Logistics"')
    assert res.backend == "ddg" and len(res.hits) == 2
    assert res.hits[0].link == "https://www.linkedin.com/in/dana-lee-analytics"
    assert res.hits[0].title.startswith("Dana Lee") and "Manager, Analytics" in res.hits[0].snippet
    assert res.hits[1].link == "https://acmelogistics.example/contact"


@respx.mock
def test_websearch_prefers_serper_falls_back_to_ddg_and_budgets(home, db_session, posting) -> None:
    respx.post("https://google.serper.dev/search").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "10"})
    )
    respx.get(url__regex=r"https://html\.duckduckgo\.com/html/.*").mock(
        return_value=httpx.Response(200, text=(FIX / "ddg.html").read_text())
    )
    profile = load_profile()
    budget = Budget(db_session, profile, posting_id=posting.id)
    ws = WebSearch(SerperClient(HttpClient(), "K"), DuckDuckGoClient(HttpClient()), budget)
    res = ws.search("anything", gl="ca", hl="en")
    assert res is not None and res.backend == "ddg" and "serper" in ws.last_reason
    assert budget.job_units["websearch"] == 1.0 and budget.month_units("serper") == 0
    profile.contacts.per_job_credit_cap["websearch"] = 1
    assert ws.search("again", gl="ca", hl="en") is None and "per-job cap" in ws.last_reason


def test_websearch_none_when_no_backend(home, db_session, posting) -> None:
    ws = WebSearch(None, None, Budget(db_session, load_profile(), posting_id=posting.id))
    assert ws.search("x", gl="ca", hl="en") is None and "no search backend" in ws.last_reason


@respx.mock
def test_ddg_challenge_page_is_rate_limiting_not_empty_results() -> None:
    from jobfinder.discovery.fetch import RateLimited

    respx.get(url__regex=r"https://html\.duckduckgo\.com/html/.*").mock(
        return_value=httpx.Response(200, text=(FIX / "ddg_challenge.html").read_text())
    )
    with pytest.raises(RateLimited) as exc:
        DuckDuckGoClient(HttpClient()).search("anything")
    assert exc.value.retry_after == 3600 and "challenge" in str(exc.value)


@respx.mock
def test_websearch_marks_rate_limited_backend_dead_for_run(home, db_session, posting) -> None:
    route = respx.get(url__regex=r"https://html\.duckduckgo\.com/html/.*").mock(
        return_value=httpx.Response(200, text=(FIX / "ddg_challenge.html").read_text())
    )
    budget = Budget(db_session, load_profile(), posting_id=posting.id)
    ws = WebSearch(None, DuckDuckGoClient(HttpClient()), budget)
    assert ws.search("one", gl="ca", hl="en") is None and "challenge" in ws.last_reason
    assert ws.search("two", gl="ca", hl="en") is None
    assert "unavailable for this run" in ws.last_reason
    assert route.call_count == 1 and "websearch" not in budget.job_units


@respx.mock
def test_ddg_paces_consecutive_requests(monkeypatch) -> None:
    from jobfinder.contacts.providers import search as search_mod

    respx.get(url__regex=r"https://html\.duckduckgo\.com/html/.*").mock(
        return_value=httpx.Response(200, text=(FIX / "ddg.html").read_text())
    )
    slept: list[float] = []
    monkeypatch.setattr(search_mod, "_sleep", lambda s: slept.append(s))
    client = DuckDuckGoClient(HttpClient(), pause_s=1.5)
    client.search("one")
    client.search("two")
    assert slept == [1.5]
