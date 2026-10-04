from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from jobfinder.db.models import Posting
from jobfinder.db.session import get_engine
from jobfinder.discovery.fetch import HttpClient

FIX = Path(__file__).parent.parent / "fixtures" / "manual"
GH = "https://boards.greenhouse.io/acme/jobs/7"


def _serve(monkeypatch, status: int, body: str) -> None:
    """Every fetch from the Add page answers `status`/`body` — no network, no respx."""
    transport = httpx.MockTransport(lambda request: httpx.Response(status, text=body))
    monkeypatch.setattr(
        "jobfinder.app.routes.add._http_client",
        lambda: HttpClient(retries=0, transport=transport),
    )


def _jsonld() -> str:
    return (FIX / "greenhouse_jsonld.html").read_text(encoding="utf-8")


def test_add_page_renders(client) -> None:
    r = client.get("/add")
    assert r.status_code == 200 and 'name="url"' in r.text and "Add job" in r.text


def test_preview_rejects_bad_url(client) -> None:
    r = client.post("/add/preview", data={"url": "ftp://nope", "text": ""})
    assert r.status_code == 200 and "http(s)" in r.text and 'name="title"' not in r.text


def test_preview_prefills_from_jsonld(client, monkeypatch) -> None:
    _serve(monkeypatch, 200, _jsonld())
    r = client.post("/add/preview", data={"url": GH, "text": ""})
    assert r.status_code == 200
    assert 'value="Data Analyst"' in r.text and 'value="Acme Analytics"' in r.text
    assert "structured data" in r.text


def test_preview_blocked_asks_for_description(client, monkeypatch) -> None:
    _serve(monkeypatch, 403, "denied")
    r = client.post("/add/preview", data={"url": GH, "text": ""})
    assert "403" in r.text and "paste the job description" in r.text.lower()


def test_save_requires_title_and_company(client) -> None:
    r = client.post("/add", data={
        "url": GH, "title": "Data Analyst", "company": "", "location": "",
        "description": "x", "posted_at": "",
    }, follow_redirects=False)
    assert r.status_code == 200 and "required" in r.text


def test_save_shortlists_and_starts_scoring(client, monkeypatch) -> None:
    started: list[int] = []
    monkeypatch.setattr(
        "jobfinder.app.routes.add.start_score_thread", lambda pid, llm: started.append(pid)
    )
    r = client.post("/add", data={
        "url": GH, "title": "Data Analyst", "company": "Acme Analytics",
        "location": "Montreal, QC", "description": "Build dashboards. " * 30,
        "posted_at": "2026-09-30T00:00:00",
    }, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("?added=1")
    with Session(get_engine()) as s:
        p = s.query(Posting).one()
        assert p.pipeline.stage == "shortlisted" and started == [p.id]
    page = client.get(r.headers["location"])
    assert "Added to Shortlist" in page.text and "scoring" in page.text.lower()


def test_preview_flags_existing_posting(client, monkeypatch) -> None:
    client.post("/add", data={
        "url": GH, "title": "Data Analyst", "company": "Acme Analytics", "location": "",
        "description": "Build dashboards. " * 30, "posted_at": "",
    })
    _serve(monkeypatch, 200, _jsonld())
    r = client.post("/add/preview", data={"url": GH, "text": ""})
    assert "already in jobfinder" in r.text.lower()


def test_save_with_bad_url_goes_back_to_step_one(client) -> None:
    r = client.post("/add", data={
        "url": "javascript:alert(1)", "title": "T", "company": "C", "location": "",
        "description": "", "posted_at": "",
    }, follow_redirects=False)
    assert r.status_code == 200 and "http(s)" in r.text and 'name="title"' not in r.text
