from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from jobfinder.app.background import claim_scoring, start_score_thread
from jobfinder.app.deps import current_profile, db_session, get_llm_dep, templates
from jobfinder.app.routes.inbox import maybe_start_contacts
from jobfinder.config import Profile
from jobfinder.discovery.fetch import HttpClient
from jobfinder.discovery.manual import (
    THIN_CHARS,
    ParsedJob,
    canonical_url,
    find_by_url,
    parse_url,
    save_manual,
)

router = APIRouter(prefix="/add")

ORIGIN_LABELS = {
    "jsonld": "Read from the page's structured data.",
    "llm": "Read by the LLM from the page text — check the fields.",
    "none": "Could not read the fields — fill them in.",
}


def _http_client() -> HttpClient:
    """Seam for tests: they swap in an httpx.MockTransport (respx is not used beside TestClient)."""
    return HttpClient(timeout=20.0, retries=1)


def _page(request: Request, **ctx):  # noqa: ANN003, ANN202
    base = {"active": "add", "step": 1, "url": "", "text": "", "error": None}
    return templates.TemplateResponse(request, "add.html", {**base, **ctx})


@router.get("")
def add_form(request: Request):  # noqa: ANN201
    return _page(request)


@router.post("/preview")
def preview(
    request: Request,
    url: str = Form(""),
    text: str = Form(""),
    session: Session = Depends(db_session),
):  # noqa: ANN201
    try:
        canonical_url(url)
    except ValueError:
        return _page(request, url=url, text=text, error="Paste a full http(s) link to the posting.")
    parsed = parse_url(url, text=text or None, client=_http_client(), llm=get_llm_dep(request))
    return _page(
        request, step=2, job=parsed, origin_label=ORIGIN_LABELS[parsed.origin],
        existing=find_by_url(session, parsed.url),
    )


@router.post("")
def save(
    request: Request,
    url: str = Form(...),
    title: str = Form(""),
    company: str = Form(""),
    location: str = Form(""),
    description: str = Form(""),
    posted_at: str = Form(""),
    session: Session = Depends(db_session),
    profile: Profile = Depends(current_profile),
):  # noqa: ANN201
    desc = description.strip()
    try:
        url = canonical_url(url)
    except ValueError:
        return _page(request, error="Paste a full http(s) link to the posting.")
    try:
        when = datetime.fromisoformat(posted_at) if posted_at else None
    except ValueError:
        when = None
    job = ParsedJob(
        url=url, title=title.strip(), company=company.strip(),
        location=location.strip() or None, description=desc, posted_at=when, origin="none",
        fetch_error=None, thin=len(desc) < THIN_CHARS,
    )
    if not job.title or not job.company:
        return _page(
            request, step=2, job=job, origin_label="", existing=None,
            error="Title and company are required.",
        )
    posting, _created = save_manual(
        session, job, match_threshold=profile.scoring.match_threshold
    )
    llm = get_llm_dep(request)
    if posting.latest_score is None and claim_scoring(posting.id):
        start_score_thread(posting.id, llm)
    maybe_start_contacts(session, posting.id, profile, llm)
    return RedirectResponse(f"/jobs/{posting.id}?added=1", status_code=303)
