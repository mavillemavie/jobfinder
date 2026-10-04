from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select

from jobfinder.db.models import Company, Pipeline, Posting, PostingSource, Score, utcnow
from jobfinder.discovery.fetch import HttpClient
from jobfinder.discovery.manual import (
    THIN_CHARS,
    ParsedJob,
    canonical_url,
    find_by_url,
    parse_jsonld,
    parse_url,
    save_manual,
)
from jobfinder.llm.base import LLMError
from jobfinder.llm.fake import FakeLLM

FIX = Path(__file__).parent.parent / "fixtures" / "manual"
GH = "https://boards.greenhouse.io/acme/jobs/7"
PLAIN = "https://careers.gamma.test/jobs/42"


def _html(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_canonical_url_strips_tracking_and_fragment() -> None:
    url = "https://boards.greenhouse.io/acme/jobs/7?gh_jid=7&utm_source=x&trk=abc#apply"
    assert canonical_url(url) == "https://boards.greenhouse.io/acme/jobs/7?gh_jid=7"


@pytest.mark.parametrize("bad", ["", "ftp://x.test/a", "not a url", "https://"])
def test_canonical_url_rejects_non_http(bad: str) -> None:
    with pytest.raises(ValueError):
        canonical_url(bad)


def test_jsonld_jobposting_with_address() -> None:
    got = parse_jsonld(_html("greenhouse_jsonld.html"))
    assert got["title"] == "Data Analyst" and got["company"] == "Acme Analytics"
    assert got["location"] == "Montreal, QC, CA"
    assert got["posted_at"] == datetime(2026, 9, 30)
    assert "Power BI" in got["description"] and "<p>" not in got["description"]
    assert "3+ years of analytics" in got["description"]


def test_jsonld_graph_string_org_and_telecommute_skip_malformed() -> None:
    got = parse_jsonld(_html("graph_jsonld.html"))
    assert got["title"] == "BI Developer" and got["company"] == "Beta Corp"
    assert got["location"] == "Remote"


def test_jsonld_absent_returns_none() -> None:
    assert parse_jsonld(_html("no_jsonld.html")) is None
    assert parse_jsonld("") is None


def _client() -> HttpClient:
    return HttpClient(retries=0)


@respx.mock
def test_parse_url_jsonld_needs_no_llm() -> None:
    respx.get(GH).mock(return_value=httpx.Response(200, text=_html("greenhouse_jsonld.html")))
    llm = FakeLLM()
    got = parse_url(GH + "?utm_source=mail", text=None, client=_client(), llm=llm)
    assert got.url == GH and got.origin == "jsonld" and got.fetch_error is None
    assert (got.title, got.company) == ("Data Analyst", "Acme Analytics")
    assert got.thin is False and llm.calls == []


@respx.mock
def test_parse_url_without_jsonld_uses_llm() -> None:
    respx.get(PLAIN).mock(return_value=httpx.Response(200, text=_html("no_jsonld.html")))
    llm = FakeLLM({"extract_posting": {
        "title": "Reporting Analyst", "company": "Gamma Inc", "location": "Toronto, ON",
    }})
    got = parse_url(PLAIN, text=None, client=_client(), llm=llm)
    assert got.origin == "llm" and got.company == "Gamma Inc" and got.location == "Toronto, ON"
    assert "KPI reports" in got.description and "© Gamma" not in got.description
    call = llm.calls[0]
    assert call["tier"] == "fast" and "Reporting Analyst | Gamma Inc" in call["user"]


@respx.mock
def test_parse_url_llm_failure_leaves_fields_empty() -> None:
    respx.get(PLAIN).mock(return_value=httpx.Response(200, text=_html("no_jsonld.html")))

    def boom(user: str) -> dict:
        raise LLMError("cli login expired")

    got = parse_url(PLAIN, text=None, client=_client(), llm=FakeLLM({"extract_posting": boom}))
    assert got.origin == "none" and got.title == "" and got.company == ""
    assert "KPI reports" in got.description


@respx.mock
def test_parse_url_blocked_uses_pasted_text() -> None:
    respx.get(PLAIN).mock(return_value=httpx.Response(403, text="denied"))
    pasted = "Reporting Analyst at Gamma Inc. " * 20
    llm = FakeLLM({"extract_posting": {
        "title": "Reporting Analyst", "company": "Gamma Inc", "location": "",
    }})
    got = parse_url(PLAIN, text=pasted, client=_client(), llm=llm)
    assert got.fetch_error == "403" and got.description == pasted.strip()
    assert got.origin == "llm" and got.location is None and got.thin is False


@respx.mock
def test_parse_url_blocked_without_text_is_thin_and_skips_llm() -> None:
    respx.get(PLAIN).mock(return_value=httpx.Response(403, text="denied"))
    llm = FakeLLM()
    got = parse_url(PLAIN, text=None, client=_client(), llm=llm)
    assert got.fetch_error == "403" and got.thin is True and got.description == ""
    assert llm.calls == []


@respx.mock
def test_parse_url_longer_pasted_text_wins_over_fetched() -> None:
    respx.get(GH).mock(return_value=httpx.Response(200, text=_html("greenhouse_jsonld.html")))
    pasted = "Full description pasted from the browser. " * 40
    got = parse_url(GH, text=pasted, client=_client(), llm=FakeLLM())
    assert got.description == pasted.strip() and got.origin == "jsonld"


@respx.mock
def test_parse_url_linkedin_view_fetches_guest_endpoint() -> None:
    view = "https://www.linkedin.com/jobs/view/4012345678/"
    guest = respx.get("https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4012345678").mock(
        return_value=httpx.Response(200, text=_html("linkedin_guest.html"))
    )
    llm = FakeLLM({"extract_posting": {
        "title": "Data Engineer", "company": "Delta Systems", "location": "Vancouver, BC",
    }})
    got = parse_url(view, text=None, client=_client(), llm=llm)
    assert guest.called and got.url == view
    assert got.description.startswith("Delta Systems is looking for")
    assert got.origin == "llm" and got.company == "Delta Systems"
    assert got.thin is (len(got.description) < THIN_CHARS)


def _parsed(**kw) -> ParsedJob:
    base = dict(
        url=GH, title="Data Analyst", company="Acme Analytics", location="Montreal, QC",
        description="Build dashboards. " * 30, posted_at=None, origin="jsonld",
        fetch_error=None, thin=False,
    )
    return ParsedJob(**{**base, **kw})


def test_save_manual_new_posting_is_shortlisted(db_session) -> None:
    p, created = save_manual(db_session, _parsed())
    assert created is True and p.status == "new" and p.pipeline.stage == "shortlisted"
    assert p.prefilter_result == {"passed": True, "reason": "manual", "manual": True}
    assert p.sources[0].source == "manual" and p.apply_url == GH
    assert find_by_url(db_session, GH).id == p.id


def test_save_manual_twice_is_one_posting(db_session) -> None:
    p1, _ = save_manual(db_session, _parsed())
    p2, created = save_manual(db_session, _parsed())
    assert created is False and p2.id == p1.id
    assert len(db_session.scalars(select(Posting)).all()) == 1


def test_save_manual_old_posting_is_not_stale(db_session) -> None:
    p, _ = save_manual(db_session, _parsed(posted_at=utcnow() - timedelta(days=400)))
    assert p.status == "new" and p.pipeline.stage == "shortlisted"


def test_save_manual_reopens_a_dismissed_posting(db_session) -> None:
    p, _ = save_manual(db_session, _parsed())
    p.status, p.dismiss_reason = "dismissed", "too_senior"
    p.pipeline.stage, p.pipeline.close_reason = "closed", "withdrawn"
    db_session.commit()
    p2, _ = save_manual(db_session, _parsed())
    assert p2.status == "new" and p2.dismiss_reason is None
    assert p2.pipeline.stage == "shortlisted" and p2.pipeline.close_reason is None


@pytest.mark.parametrize("stage,reason", [("applied", None), ("closed", "rejected")])
def test_save_manual_never_moves_a_real_outcome(db_session, stage, reason) -> None:
    p, _ = save_manual(db_session, _parsed())
    p.pipeline.stage, p.pipeline.close_reason = stage, reason
    db_session.commit()
    p2, _ = save_manual(db_session, _parsed())
    assert (p2.pipeline.stage, p2.pipeline.close_reason) == (stage, reason)


def test_save_manual_requires_title_and_company(db_session) -> None:
    with pytest.raises(ValueError):
        save_manual(db_session, _parsed(company=" "))
    assert db_session.scalar(select(Pipeline)) is None


@respx.mock
def test_parse_url_llm_sees_the_page_header_not_just_the_description() -> None:
    # Live check 2026-10-04: Greenhouse job-boards and LinkedIn guest pages carry no JSON-LD and
    # keep title/location in a header outside the description element.
    view = "https://www.linkedin.com/jobs/view/4012345678/"
    respx.get("https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4012345678").mock(
        return_value=httpx.Response(200, text=_html("linkedin_guest.html"))
    )
    llm = FakeLLM({"extract_posting": {
        "title": "Data Engineer", "company": "Delta Systems", "location": "Vancouver, BC",
    }})
    got = parse_url(view, text=None, client=_client(), llm=llm)
    user = llm.calls[0]["user"]
    assert "Data Engineer" in user.split("Delta Systems is looking for")[0]
    assert "Vancouver, BC" in user.split("Delta Systems is looking for")[0]
    assert got.description.startswith("Delta Systems is looking for")


def test_canonical_url_keeps_query_untouched_when_nothing_is_stripped() -> None:
    url = "https://jobs.test/view?id=a%2Fb&q=data+analyst"
    assert canonical_url(url) == url


def _scanned(db_session, *, status="dismissed", url=GH) -> Posting:
    """A posting a scan stored under a different place than the manual add will compute."""
    p = Posting(
        company=Company(name="Acme Analytics", normalized_name="acme analytics"),
        title="Data Analyst", normalized_title="data analyst", city="Toronto",
        apply_url=url, dedupe_key="scan-key", content_hash="h", status=status,
        description_text="short", dismiss_reason="too_senior" if status == "dismissed" else None,
    )
    p.sources.append(PostingSource(source="linkedin_guest", source_id="1", url=url))
    db_session.add(p)
    db_session.commit()
    return p


def test_save_manual_reuses_the_posting_found_by_url(db_session) -> None:
    # Review finding: the preview says "already in jobfinder", so the save must update that row
    # even when the edited title/location give a different dedupe key.
    scanned = _scanned(db_session)
    p, created = save_manual(db_session, _parsed(location=None, title="Data Analyst II"))
    assert created is False and p.id == scanned.id
    assert p.status == "new" and p.pipeline.stage == "shortlisted"
    assert len(db_session.scalars(select(Posting)).all()) == 1
    assert {s.source for s in p.sources} == {"linkedin_guest", "manual"}
    assert p.description_text.startswith("Build dashboards.")  # longer text replaces the snippet


def test_save_manual_restores_status_from_an_existing_score(db_session) -> None:
    scanned = _scanned(db_session)
    db_session.add(Score(
        posting_id=scanned.id, model="fake", fit_score=90, reasons=[], missing_requirements=[],
        seniority_match="match", eligibility={}, red_flags=[], one_line_summary="", raw={},
    ))
    db_session.commit()
    db_session.refresh(scanned)
    p, _ = save_manual(db_session, _parsed(), match_threshold=70)
    assert p.status == "match"
    p.status = "dismissed"
    db_session.commit()
    p, _ = save_manual(db_session, _parsed(), match_threshold=95)
    assert p.status == "scored"


def test_save_manual_thin_description_stays_hydratable(db_session) -> None:
    p, _ = save_manual(db_session, _parsed(description="Teaser only.", thin=True))
    assert p.description_complete is False


def test_save_manual_long_urls_do_not_collide(db_session) -> None:
    base = "https://jobs.test/" + "x" * 300
    p1, _ = save_manual(db_session, _parsed(url=base + "1", title="Data Analyst"))
    p2, _ = save_manual(db_session, _parsed(url=base + "2", title="BI Developer"))
    assert p1.id != p2.id
    assert p1.sources[0].source_id != p2.sources[0].source_id
