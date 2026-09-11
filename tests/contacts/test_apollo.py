import json
from pathlib import Path

import httpx
import respx

from jobfinder.contacts.providers.apollo import ApolloClient
from jobfinder.discovery.fetch import HttpClient

FIX = Path(__file__).parent.parent / "fixtures" / "contacts"


@respx.mock
def test_people_search_uses_query_params_and_parses() -> None:
    route = respx.post(url__regex=r"https://api\.apollo\.io/api/v1/mixed_people/api_search.*").mock(
        return_value=httpx.Response(200, json=json.loads((FIX / "apollo_search.json").read_text()))
    )
    people = ApolloClient(HttpClient(), "AK").people_search(
        "acmelogistics.example", ["Manager, Analytics", "Talent Acquisition"]
    )
    req = route.calls[0].request
    assert req.headers["x-api-key"] == "AK"
    titles = req.url.params.get_list("person_titles[]")
    assert titles == ["Manager, Analytics", "Talent Acquisition"]
    assert req.url.params.get_list("q_organization_domains_list[]") == ["acmelogistics.example"]
    assert req.url.params["per_page"] == "5"
    assert [p.first_name for p in people] == ["Dana", "Sam", "Ana"]
    assert people[0].has_email is True and people[0].has_direct_phone == "Yes"
    assert people[0].last_name_obfuscated == "L."
    assert people[2].has_email is False and people[2].has_direct_phone is None


@respx.mock
def test_people_match_parses_email() -> None:
    route = respx.post("https://api.apollo.io/api/v1/people/match").mock(
        return_value=httpx.Response(200, json=json.loads((FIX / "apollo_match.json").read_text()))
    )
    m = ApolloClient(HttpClient(), "AK").people_match("Dana", "Lee", "acmelogistics.example")
    body = json.loads(route.calls[0].request.content)
    assert body == {
        "first_name": "Dana", "last_name": "Lee", "domain": "acmelogistics.example",
        "reveal_personal_emails": False, "reveal_phone_number": False,
    }
    assert m is not None and m.email == "dana.lee@acmelogistics.example"
    assert m.title == "Manager, Analytics"


@respx.mock
def test_people_match_none_when_not_found() -> None:
    respx.post("https://api.apollo.io/api/v1/people/match").mock(
        return_value=httpx.Response(200, json={"person": None})
    )
    assert ApolloClient(HttpClient(), "AK").people_match("No", "Body", "x.example") is None
