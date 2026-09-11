import json
from pathlib import Path

import httpx
import respx

from jobfinder.contacts.providers.hunter import HunterClient
from jobfinder.discovery.fetch import HttpClient

FIX = Path(__file__).parent.parent / "fixtures" / "contacts"


@respx.mock
def test_domain_search_limit_1_and_pattern() -> None:
    route = respx.get(url__regex=r"https://api\.hunter\.io/v2/domain-search.*").mock(
        return_value=httpx.Response(200, json=json.loads((FIX / "hunter_domain.json").read_text()))
    )
    d = HunterClient(HttpClient(), "HK").domain_search("acmelogistics.example")
    p = route.calls[0].request.url.params
    assert p["domain"] == "acmelogistics.example" and p["limit"] == "1" and p["api_key"] == "HK"
    assert d.pattern == "{first}.{last}" and d.accept_all is False
    assert d.sample_email == "info@acmelogistics.example"


@respx.mock
def test_email_finder_and_verifier() -> None:
    respx.get(url__regex=r"https://api\.hunter\.io/v2/email-finder.*").mock(
        return_value=httpx.Response(200, json=json.loads((FIX / "hunter_finder.json").read_text()))
    )
    respx.get(url__regex=r"https://api\.hunter\.io/v2/email-verifier.*").mock(
        return_value=httpx.Response(
            200, json=json.loads((FIX / "hunter_verifier.json").read_text())
        )
    )
    c = HunterClient(HttpClient(), "HK")
    f = c.email_finder("acmelogistics.example", "Dana", "Lee")
    assert f.email == "dana.lee@acmelogistics.example" and f.score == 92 and f.status == "valid"
    v = c.email_verifier("dana.lee@acmelogistics.example")
    assert (v.status, v.result, v.accept_all, v.smtp_check, v.mx_records) == (
        "valid", "deliverable", False, True, True,
    )
