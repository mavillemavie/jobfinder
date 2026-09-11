from __future__ import annotations

import pytest

from jobfinder.config import Profile, load_profile
from jobfinder.contacts.base import ContactContext
from jobfinder.contacts.budget import Budget
from jobfinder.db.models import Company, Posting
from jobfinder.discovery.fetch import HttpClient
from jobfinder.llm.fake import FakeLLM
from jobfinder.settings import Settings


def make_posting(
    db_session,
    *,
    name: str = "Acme Logistics",
    domain: str | None = "acmelogistics.example",
    website: str | None = None,
    apply_url: str = "https://acmelogistics.example/jobs/1",
    description: str = "",
    language: str = "en",
    country: str = "CA",
    status: str = "match",
    dedupe_key: str = "k-acme-1",
    title: str = "Manager, Analytics",
) -> Posting:
    """One company + one posting so FK-checked rows (spend_ledger, contacts) have a target."""
    co = Company(
        name=name, normalized_name=name.lower(), domain=domain,
        website=website or (f"https://{domain}" if domain else None), country=country,
    )
    db_session.add(co)
    db_session.flush()
    p = Posting(
        company_id=co.id, title=title, normalized_title=title.lower(), apply_url=apply_url,
        dedupe_key=dedupe_key, content_hash="h1", country=country, status=status,
        language=language, description_text=description, description_complete=True,
    )
    db_session.add(p)
    db_session.commit()
    return p


@pytest.fixture
def posting(db_session) -> Posting:
    return make_posting(db_session)


def make_settings(**kw) -> Settings:
    return Settings(_env_file=None, llm_provider="fake", **kw)


def make_ctx(
    db_session,
    posting: Posting,
    *,
    llm=None,
    search=None,
    apollo=None,
    hunter=None,
    http: HttpClient | None = None,
    profile: Profile | None = None,
    settings: Settings | None = None,
) -> ContactContext:
    profile = profile or load_profile()
    return ContactContext(
        session=db_session, posting=posting, company=posting.company, profile=profile,
        settings=settings or make_settings(), llm=llm or FakeLLM(),
        http=http or HttpClient(retries=0), budget=Budget(db_session, profile, posting.id),
        search=search, apollo=apollo, hunter=hunter,
        language=posting.language if posting.language in ("en", "fr") else "en",
        domain=posting.company.domain, website=posting.company.website,
    )
