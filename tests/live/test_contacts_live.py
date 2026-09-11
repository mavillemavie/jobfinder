import os

import pytest

from jobfinder.contacts.providers.apollo import ApolloClient
from jobfinder.contacts.providers.hunter import HunterClient
from jobfinder.contacts.providers.search import DuckDuckGoClient, SerperClient
from jobfinder.discovery.fetch import HttpClient
from jobfinder.settings import get_settings

pytestmark = pytest.mark.live


def test_ddg_keyless_search_returns_hits() -> None:
    res = DuckDuckGoClient(HttpClient()).search('site:linkedin.com/in "Shopify" "Data Analyst"')
    assert res.backend == "ddg" and len(res.hits) >= 1
    assert any("linkedin.com/in/" in h.link for h in res.hits)


@pytest.mark.skipif(not get_settings().serper_api_key, reason="no SERPER_API_KEY")
def test_serper_search_live() -> None:
    client = SerperClient(HttpClient(), get_settings().serper_api_key)
    res = client.search('"Shopify" official site')
    assert res.hits and any("shopify" in h.link for h in res.hits)


@pytest.mark.skipif(not get_settings().apollo_api_key, reason="no APOLLO_API_KEY")
def test_apollo_people_search_is_free() -> None:
    people = ApolloClient(HttpClient(), get_settings().apollo_api_key).people_search(
        "shopify.com", ["Data Analyst"], per_page=2
    )
    assert isinstance(people, list)


@pytest.mark.skipif(
    not (get_settings().hunter_api_key and os.environ.get("JOBFINDER_LIVE_SPEND") == "1"),
    reason="needs HUNTER_API_KEY and JOBFINDER_LIVE_SPEND=1 (costs 0.5 credit)",
)
def test_hunter_verifier_live_costs_half_credit() -> None:
    client = HunterClient(HttpClient(), get_settings().hunter_api_key)
    v = client.email_verifier("support@hunter.io")
    assert v.status in ("valid", "accept_all", "unknown", "invalid", "webmail")
