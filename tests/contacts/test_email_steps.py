import json
from pathlib import Path

import httpx
import respx

from jobfinder.config import load_profile
from jobfinder.contacts.base import Person
from jobfinder.contacts.confidence import email_confidence, person_confidence, phone_confidence
from jobfinder.contacts.providers.apollo import ApolloClient
from jobfinder.contacts.providers.hunter import HunterClient
from jobfinder.contacts.steps.email_pattern import EmailPatternStep
from jobfinder.contacts.steps.email_resolve import EmailResolveStep, apply_pattern, candidates
from jobfinder.discovery.fetch import HttpClient
from tests.contacts.conftest import make_ctx, make_posting

FIX = Path(__file__).parent.parent / "fixtures" / "contacts"
HUNTER = "https://api.hunter.io/v2/"


def _verify_json(status: str, **kw) -> dict:
    base = json.loads((FIX / "hunter_verifier.json").read_text())
    base["data"].update({"status": status, **kw})
    return base


def test_confidence_weights() -> None:
    assert email_confidence("verified", "posting") == 0.95
    assert email_confidence("verified", "hunter_verifier") == 0.85
    assert email_confidence("unverified", "apollo") == 0.6
    assert email_confidence("catch_all", "pattern") == 0.4
    assert email_confidence("guessed", "hunter_verifier") == 0.25
    assert email_confidence("invalid", "hunter_verifier") == 0.0
    assert phone_confidence("direct", "posting") == 0.95
    assert phone_confidence("switchboard", "serper_kg") == 0.9
    assert phone_confidence(None, None) == 0.0
    p = Person(full_name="Dana Lee", title="Manager", title_relevance=1.0)
    assert person_confidence(p) == 0.3
    p.email, p.email_status, p.email_source = "d@a.example", "verified", "hunter_verifier"
    assert person_confidence(p) == 0.85
    p.title_relevance = 0.5
    assert person_confidence(p) == 0.68
    q = Person(full_name="Sam Roy", phone="+1 514-555-0100", phone_kind="switchboard",
               phone_source="serper_kg", title_relevance=1.0)
    assert person_confidence(q) == 0.3  # a switchboard says nothing about the person


def test_pattern_and_candidates() -> None:
    assert apply_pattern("{first}.{last}", "Émilie", "Côté", "acme.example") == (
        "emilie.cote@acme.example"
    )
    assert apply_pattern("{f}{last}", "Dana", "Lee", "acme.example") == "dlee@acme.example"
    assert apply_pattern("{first}", "Dana", "", "acme.example") == "dana@acme.example"
    assert apply_pattern("{f}{last}", "Dana", "", "acme.example") is None
    assert apply_pattern("{weird}", "Dana", "Lee", "acme.example") is None
    assert candidates("Dana", "Lee", "acme.example", "{first}.{last}") == ["dana.lee@acme.example"]
    assert candidates("Dana", "Lee", "acme.example") == [
        "dana.lee@acme.example", "dlee@acme.example", "dana@acme.example", "danalee@acme.example",
    ]
    acc = candidates("Émilie", "Côté", "acme.example")
    assert acc[:3] == ["emilie.cote@acme.example", "ecote@acme.example", "emilie@acme.example"]
    assert acc[3] == "émilie.côté@acme.example" and len(acc) == 4


@respx.mock
def test_email_pattern_spends_one_credit_and_caches(db_session) -> None:
    route = respx.get(url__regex=HUNTER + r"domain-search.*").mock(
        return_value=httpx.Response(200, json=json.loads((FIX / "hunter_domain.json").read_text()))
    )
    p = make_posting(db_session)
    ctx = make_ctx(db_session, p, hunter=HunterClient(HttpClient(), "HK"))
    ctx.people = [Person(full_name="Dana Lee", title="Manager")]
    res = EmailPatternStep().run(ctx)
    assert res.ok and res.credits == {"hunter": 1.0} and res.notes["pattern"] == "{first}.{last}"
    assert p.company.email_pattern == "{first}.{last}" and p.company.catch_all is False
    assert p.company.pattern_checked_at is not None
    assert ctx.notes["hunter_sample_email"] == "info@acmelogistics.example"
    assert ctx.budget.job_units["hunter"] == 1.0
    # second run: cached → no call, no spend
    res2 = EmailPatternStep().run(ctx)
    assert res2.skipped == "cached" and route.call_count == 1


def test_email_pattern_skip_reasons(db_session) -> None:
    p = make_posting(db_session)
    ctx = make_ctx(db_session, p, hunter=HunterClient(HttpClient(), "HK"))
    assert EmailPatternStep().run(ctx).skipped == "no named person"
    ctx.people = [Person(full_name="Dana L.", evidence={"last_name_obfuscated": True})]
    assert EmailPatternStep().run(ctx).skipped == "no named person"
    ctx.people = [Person(full_name="Dana Lee", email="d@a.example", email_status="verified")]
    assert EmailPatternStep().run(ctx).skipped == "email already known"
    ctx.people = [Person(full_name="Dana Lee")]
    ctx.hunter = None
    assert EmailPatternStep().run(ctx).skipped == "no hunter key"
    ctx.domain = None
    assert EmailPatternStep().run(ctx).skipped == "no domain"


@respx.mock
def test_resolve_with_cached_pattern_verifies_and_stops_at_first_valid(db_session) -> None:
    route = respx.get(url__regex=HUNTER + r"email-verifier.*").mock(
        return_value=httpx.Response(200, json=_verify_json("valid", smtp_check=True))
    )
    p = make_posting(db_session)
    p.company.email_pattern = "{first}.{last}"
    ctx = make_ctx(db_session, p, hunter=HunterClient(HttpClient(), "HK"))
    ctx.people = [
        Person(full_name="Dana Lee", title="Manager, Analytics", title_relevance=1.0),
        Person(full_name="Sam Roy", title="Talent Acquisition Partner", title_relevance=0.6),
    ]
    res = EmailResolveStep().run(ctx)
    assert res.ok and res.credits == {"hunter": 0.5} and res.notes["verified"] == 1
    assert route.call_count == 1
    assert route.calls[0].request.url.params["email"] == "dana.lee@acmelogistics.example"
    dana, sam = ctx.people
    assert (dana.email, dana.email_status, dana.email_source) == (
        "dana.lee@acmelogistics.example", "verified", "hunter_verifier"
    )
    assert dana.evidence["verifier"][0]["status"] == "valid" and sam.email is None
    assert ctx.has_verified_email()


@respx.mock
def test_resolve_without_pattern_walks_candidates_until_budget(db_session) -> None:
    route = respx.get(url__regex=HUNTER + r"email-verifier.*").mock(
        side_effect=[
            httpx.Response(200, json=_verify_json("invalid")),
            httpx.Response(200, json=_verify_json("unknown")),
        ]
    )
    p = make_posting(db_session)
    profile = load_profile()
    profile.contacts.per_job_credit_cap["hunter"] = 1  # 2 verifications, then stop
    ctx = make_ctx(db_session, p, hunter=HunterClient(HttpClient(), "HK"), profile=profile)
    ctx.people = [Person(full_name="Dana Lee"), Person(full_name="Sam Roy")]
    res = EmailResolveStep().run(ctx)
    assert route.call_count == 2 and res.credits == {"hunter": 1.0}
    assert [c.request.url.params["email"] for c in route.calls] == [
        "dana.lee@acmelogistics.example", "dlee@acmelogistics.example",
    ]
    dana, sam = ctx.people
    assert (dana.email, dana.email_status) == ("dlee@acmelogistics.example", "guessed")
    assert sam.email is None and "per-job cap" in res.notes["stopped"]
    assert res.notes["invalid"] == 1 and res.notes["guessed"] == 1


@respx.mock
def test_resolve_catch_all_is_cached_and_never_verified(db_session) -> None:
    route = respx.get(url__regex=HUNTER + r"email-verifier.*").mock(
        return_value=httpx.Response(200, json=_verify_json("accept_all", accept_all=True))
    )
    p = make_posting(db_session)
    p.company.email_pattern = "{first}.{last}"
    ctx = make_ctx(db_session, p, hunter=HunterClient(HttpClient(), "HK"))
    ctx.people = [Person(full_name="Dana Lee"), Person(full_name="Sam Roy")]
    res = EmailResolveStep().run(ctx)
    assert route.call_count == 1 and res.credits == {"hunter": 0.5}
    assert p.company.catch_all is True and res.notes["catch_all"] == 2
    dana, sam = ctx.people
    assert (dana.email_status, dana.email_source) == ("catch_all", "hunter_verifier")
    assert (sam.email, sam.email_status, sam.email_source) == (
        "sam.roy@acmelogistics.example", "catch_all", "pattern"
    )
    assert not ctx.has_verified_email()


@respx.mock
def test_resolve_finder_then_apollo_paths(db_session) -> None:
    respx.get(url__regex=HUNTER + r"email-verifier.*").mock(
        return_value=httpx.Response(200, json=_verify_json("invalid"))
    )
    finder = respx.get(url__regex=HUNTER + r"email-finder.*").mock(
        return_value=httpx.Response(200, json=json.loads((FIX / "hunter_finder.json").read_text()))
    )
    p = make_posting(db_session)
    ctx = make_ctx(db_session, p, hunter=HunterClient(HttpClient(), "HK"))
    ctx.people = [Person(full_name="Dana Lee", title="Manager, Analytics")]
    # only one candidate can be built for a first-name-only pattern list → finder gets its credit
    ctx.people[0].last_name = ""
    res = EmailResolveStep().run(ctx)
    assert finder.call_count == 1 and res.credits == {"hunter": 1.5}
    dana = ctx.people[0]
    assert (dana.email, dana.email_status, dana.email_source) == (
        "dana.lee@acmelogistics.example", "verified", "hunter_finder"
    )

    match = respx.post("https://api.apollo.io/api/v1/people/match").mock(
        return_value=httpx.Response(200, json=json.loads((FIX / "apollo_match.json").read_text()))
    )
    ctx2 = make_ctx(db_session, p, apollo=ApolloClient(HttpClient(), "AK"))
    ctx2.people = [Person(full_name="Dana Lee", has_email=True), Person(full_name="Sam Roy")]
    res2 = EmailResolveStep().run(ctx2)
    assert match.call_count == 1 and res2.credits == {"apollo": 1.0}
    assert (ctx2.people[0].email, ctx2.people[0].email_status, ctx2.people[0].email_source) == (
        "dana.lee@acmelogistics.example", "unverified", "apollo"
    )
    assert ctx2.people[1].email is None  # no has_email signal → no Apollo credit spent


def test_resolve_skips_without_domain_or_people(db_session) -> None:
    p = make_posting(db_session)
    ctx = make_ctx(db_session, p, hunter=HunterClient(HttpClient(), "HK"))
    assert EmailResolveStep().run(ctx).skipped == "no resolvable person"
    ctx.domain = None
    assert EmailResolveStep().run(ctx).skipped == "no domain"
