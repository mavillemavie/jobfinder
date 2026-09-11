from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from jobfinder.config import Profile
    from jobfinder.contacts.budget import Budget
    from jobfinder.contacts.providers.apollo import ApolloClient
    from jobfinder.contacts.providers.hunter import HunterClient
    from jobfinder.contacts.providers.search import WebSearch
    from jobfinder.db.models import Company, Posting
    from jobfinder.discovery.fetch import HttpClient
    from jobfinder.llm.base import LLMProvider
    from jobfinder.settings import Settings

_HM = (
    "manager", "director", "head", "lead", "chief", "vp", "vice president", "gestionnaire",
    "directeur", "directrice", "chef", "responsable", "superviseur", "supervisor", "principal",
)
_TA = (
    "recruit", "recrut", "talent", "acquisition", "sourcer", "sourcing", "hr ", "human resources",
    "people ", "ressources humaines", "acquisition de talents",
)


def role_kind_for_title(title: str | None) -> str:
    t = f" {(title or '').lower()} "
    if any(k in t for k in _TA):
        return "recruiter"
    if any(k in t for k in _HM):
        return "hiring_manager"
    return "other"


def strip_diacritics(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def split_name(full_name: str) -> tuple[str, str]:
    cleaned = re.split(r",|\(", full_name)[0].strip()
    parts = [p for p in cleaned.split() if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


@dataclass
class Person:
    full_name: str | None = None
    title: str | None = None
    role_kind: str = "other"
    linkedin_url: str | None = None
    email: str | None = None
    email_status: str = "unverified"
    email_source: str | None = None
    phone: str | None = None
    phone_kind: str | None = None
    phone_source: str | None = None
    has_email: bool | None = None
    has_direct_phone: str | None = None
    title_relevance: float = 0.5
    confidence: float = 0.0
    sources: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    first_name: str = field(init=False, default="")
    last_name: str = field(init=False, default="")

    def __post_init__(self) -> None:
        if self.full_name:
            self.first_name, self.last_name = split_name(self.full_name)
        else:
            self.first_name, self.last_name = "", ""
        if self.role_kind == "other" and self.title:
            self.role_kind = role_kind_for_title(self.title)


@dataclass
class StepResult:
    name: str
    ok: bool = True
    credits: dict[str, float] = field(default_factory=dict)
    notes: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    skipped: str | None = None


@dataclass
class ContactContext:
    session: Session
    posting: Posting
    company: Company
    profile: Profile
    settings: Settings
    llm: LLMProvider
    http: HttpClient
    budget: Budget
    search: WebSearch | None = None
    apollo: ApolloClient | None = None
    hunter: HunterClient | None = None
    language: str = "en"
    domain: str | None = None
    website: str | None = None
    titles: list[str] = field(default_factory=list)
    people: list[Person] = field(default_factory=list)
    stated_emails: list[str] = field(default_factory=list)
    phones: list[tuple[str, str, str]] = field(default_factory=list)  # (number, kind, source)
    knowledge_graph: dict[str, Any] = field(default_factory=dict)
    notes: dict[str, Any] = field(default_factory=dict)

    def has_verified_email(self) -> bool:
        return any(p.email and p.email_status == "verified" for p in self.people)

    def has_any_phone(self) -> bool:
        return bool(self.phones) or any(p.phone for p in self.people)

    def stop_satisfied(self) -> bool:
        rule = self.profile.contacts.stop_when
        if rule == "verified_email_and_any_phone":
            return self.has_verified_email() and self.has_any_phone()
        if rule == "verified_email":
            return self.has_verified_email()
        return False

    @property
    def country(self) -> str:
        return (self.posting.country or self.company.country or "CA").upper()

    @property
    def gl_hl(self) -> tuple[str, str]:
        gl = "uk" if self.country == "GB" else "ca"
        return gl, ("fr" if self.language == "fr" else "en")


class ContactStep(Protocol):
    name: str

    def run(self, ctx: ContactContext) -> StepResult: ...
