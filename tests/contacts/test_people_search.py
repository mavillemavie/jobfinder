import json
from pathlib import Path

import httpx
import respx

from jobfinder.config import load_profile
from jobfinder.contacts.budget import Budget
from jobfinder.contacts.providers.apollo import ApolloClient
from jobfinder.contacts.providers.search import SerperClient, WebSearch
from jobfinder.contacts.steps.people_search import (
    PeopleSearchStep,
    parse_linkedin_hit,
    title_relevance,
)
from jobfinder.discovery.fetch import HttpClient
from tests.contacts.conftest import make_ctx, make_posting

FIX = Path(__file__).parent.parent / "fixtures" / "contacts"
TITLES = ["Manager, Analytics", "Director, Business Intelligence", "Talent Acquisition Partner"]


def test_parse_linkedin_hit() -> None:
    assert parse_linkedin_hit("Dana Lee - Manager, Analytics - Acme Logistics | LinkedIn") == (
        "Dana Lee", "Manager, Analytics", "Acme Logistics"
    )
    assert parse_linkedin_hit("Sam Roy – Recruiter | LinkedIn") == ("Sam Roy", "Recruiter", None)
    # shapes Google actually renders today (observed 2026-09-05)
    assert parse_linkedin_hit("Sam Roy - Software test specialist II at Genetec - LinkedIn") == (
        "Sam Roy", "Software test specialist II", "Genetec"
    )
    assert parse_linkedin_hit("Dana Lee - Data Analyst | Azure & BI Specialist - LinkedIn") == (
        "Dana Lee", "Data Analyst | Azure & BI Specialist", None
    )
    assert parse_linkedin_hit("Ana Pires - Data Analyst Intern @ Genetec | MEng @ Concordia") == (
        "Ana Pires", "Data Analyst Intern", "Genetec"
    )
    assert parse_linkedin_hit("Acme Logistics | LinkedIn") is None
    assert parse_linkedin_hit("Random page title") is None


def test_title_relevance() -> None:
    assert title_relevance("Manager, Analytics", TITLES) == 1.0
    assert title_relevance("Senior Manager, Analytics", TITLES) == 1.0
    assert title_relevance("Head of Data", TITLES) == 0.7
    assert title_relevance("Recruiter", TITLES) == 0.6
    assert title_relevance("Data Analyst", TITLES) == 0.3


@respx.mock
def test_apollo_and_linkedin_merge_rank_and_budget(db_session) -> None:
    respx.post(url__regex=r"https://api\.apollo\.io/api/v1/mixed_people/api_search.*").mock(
        return_value=httpx.Response(200, json=json.loads((FIX / "apollo_search.json").read_text()))
    )
    serper = respx.post("https://google.serper.dev/search").mock(
        return_value=httpx.Response(200, json=json.loads((FIX / "serper_people.json").read_text()))
    )
    p = make_posting(db_session)
    budget = Budget(db_session, load_profile(), posting_id=p.id)
    ctx = make_ctx(
        db_session, p, search=WebSearch(SerperClient(HttpClient(), "K"), None, budget),
        apollo=ApolloClient(HttpClient(), "AK"),
    )
    ctx.titles = TITLES + ["Chief Data Officer"]
    res = PeopleSearchStep().run(ctx)
    assert res.ok and res.notes["apollo"] == 3
    # only Dana and Sam are fully named (Ana P. stays obfuscated) → all 3 queries run
    assert serper.call_count == 3 and res.credits == {"websearch": 3.0}
    names = [x.full_name for x in ctx.people]
    assert names[:2] == ["Dana Lee", "Sam Roy"] and "Ana P." in names and "Pat Doe" not in names
    dana = ctx.people[0]
    assert dana.has_email is True and dana.has_direct_phone == "Yes"
    assert dana.linkedin_url == "https://ca.linkedin.com/in/dana-lee-analytics"
    assert dana.role_kind == "hiring_manager" and dana.title_relevance == 1.0
    assert "last_name_obfuscated" not in dana.evidence and dana.last_name == "Lee"
    assert set(dana.sources) == {"apollo", "linkedin_search"}
    ana = next(x for x in ctx.people if x.full_name == "Ana P.")
    assert ana.evidence["last_name_obfuscated"] is True and ana.title_relevance == 0.3
    # company quoted, title unquoted and de-punctuated: quoted titles over-constrain Google
    assert json.loads(serper.calls[0].request.content)["q"] == (
        'site:linkedin.com/in "Acme Logistics" Manager Analytics'
    )


@respx.mock
def test_search_only_runs_up_to_three_queries(db_session) -> None:
    serper = respx.post("https://google.serper.dev/search").mock(
        return_value=httpx.Response(200, json={"organic": []})
    )
    p = make_posting(db_session)
    ctx = make_ctx(
        db_session, p,
        search=WebSearch(SerperClient(HttpClient(), "K"), None,
                         Budget(db_session, load_profile(), p.id)),
    )
    ctx.titles = TITLES + ["Chief Data Officer", "VP Data"]
    res = PeopleSearchStep().run(ctx)
    assert serper.call_count == 3 and res.credits == {"websearch": 3.0} and ctx.people == []


def test_skips_without_titles_or_backends(db_session) -> None:
    p = make_posting(db_session)
    assert PeopleSearchStep().run(make_ctx(db_session, p)).skipped == "no target titles"
    ctx = make_ctx(db_session, p)
    ctx.titles = TITLES
    res = PeopleSearchStep().run(ctx)
    assert res.ok and res.notes["people"] == [] and res.credits == {}
