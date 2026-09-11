from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    normalized_name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    domain: Mapped[str | None] = mapped_column(String(255))
    website: Mapped[str | None] = mapped_column(String(500))
    ats_type: Mapped[str | None] = mapped_column(String(40))
    ats_board_token: Mapped[str | None] = mapped_column(String(255))
    main_phone: Mapped[str | None] = mapped_column(String(60))
    email_pattern: Mapped[str | None] = mapped_column(String(80))
    catch_all: Mapped[bool | None] = mapped_column(Boolean)
    pattern_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    country: Mapped[str | None] = mapped_column(String(2))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    postings: Mapped[list[Posting]] = relationship(back_populates="company")


class Posting(Base):
    __tablename__ = "postings"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    normalized_title: Mapped[str] = mapped_column(String(300), index=True)
    seniority: Mapped[str | None] = mapped_column(String(20))
    location_raw: Mapped[str | None] = mapped_column(String(300))
    country: Mapped[str | None] = mapped_column(String(2), index=True)
    region: Mapped[str | None] = mapped_column(String(60))
    city: Mapped[str | None] = mapped_column(String(120))
    remote_type: Mapped[str] = mapped_column(String(10), default="unknown")
    remote_scope: Mapped[str | None] = mapped_column(String(60))
    language: Mapped[str] = mapped_column(String(5), default="en")
    description_text: Mapped[str] = mapped_column(Text, default="")
    description_complete: Mapped[bool] = mapped_column(Boolean, default=True)
    salary_raw: Mapped[str | None] = mapped_column(String(200))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    apply_url: Mapped[str] = mapped_column(String(1000))
    dedupe_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    prefilter_result: Mapped[dict | None] = mapped_column(JSON)
    dismiss_reason: Mapped[str | None] = mapped_column(String(40))

    company: Mapped[Company] = relationship(back_populates="postings")
    sources: Mapped[list[PostingSource]] = relationship(
        back_populates="posting", cascade="all, delete-orphan"
    )
    scores: Mapped[list[Score]] = relationship(
        back_populates="posting", cascade="all, delete-orphan"
    )
    contacts: Mapped[list[Contact]] = relationship(
        back_populates="posting", cascade="all, delete-orphan"
    )
    documents: Mapped[list[Document]] = relationship(
        back_populates="posting", cascade="all, delete-orphan"
    )
    drafts: Mapped[list[Draft]] = relationship(
        back_populates="posting", cascade="all, delete-orphan"
    )
    pipeline: Mapped[Pipeline | None] = relationship(
        back_populates="posting", uselist=False, cascade="all, delete-orphan"
    )
    activities: Mapped[list[Activity]] = relationship(
        back_populates="posting", cascade="all, delete-orphan"
    )

    @property
    def latest_score(self) -> Score | None:
        return max(self.scores, key=lambda s: s.created_at, default=None)


class PostingSource(Base):
    __tablename__ = "posting_sources"
    __table_args__ = (UniqueConstraint("source", "source_id", name="uq_source_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    posting_id: Mapped[int] = mapped_column(ForeignKey("postings.id"), index=True)
    source: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(1000))
    seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    posting: Mapped[Posting] = relationship(back_populates="sources")


class Score(Base):
    __tablename__ = "scores"
    id: Mapped[int] = mapped_column(primary_key=True)
    posting_id: Mapped[int] = mapped_column(ForeignKey("postings.id"), index=True)
    model: Mapped[str] = mapped_column(String(60))
    fit_score: Mapped[int] = mapped_column(Integer)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    missing_requirements: Mapped[list] = mapped_column(JSON, default=list)
    seniority_match: Mapped[str] = mapped_column(String(10), default="match")
    eligibility: Mapped[dict] = mapped_column(JSON, default=dict)
    red_flags: Mapped[list] = mapped_column(JSON, default=list)
    one_line_summary: Mapped[str] = mapped_column(String(300), default="")
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    posting: Mapped[Posting] = relationship(back_populates="scores")


class Contact(Base):
    __tablename__ = "contacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    posting_id: Mapped[int] = mapped_column(ForeignKey("postings.id"), index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    full_name: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(String(200))
    role_kind: Mapped[str] = mapped_column(String(20), default="other")
    email: Mapped[str | None] = mapped_column(String(255))
    email_status: Mapped[str] = mapped_column(String(20), default="unverified")
    email_source: Mapped[str | None] = mapped_column(String(40))
    phone: Mapped[str | None] = mapped_column(String(60))
    phone_kind: Mapped[str | None] = mapped_column(String(20))
    phone_source: Mapped[str | None] = mapped_column(String(40))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    posting: Mapped[Posting] = relationship(back_populates="contacts")
    company: Mapped[Company] = relationship()
    drafts: Mapped[list[Draft]] = relationship(back_populates="contact")


class ContactRun(Base):
    __tablename__ = "contact_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    posting_id: Mapped[int] = mapped_column(ForeignKey("postings.id"), index=True)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="running")
    credits: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    posting_id: Mapped[int] = mapped_column(ForeignKey("postings.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    language: Mapped[str] = mapped_column(String(5), default="en")
    format: Mapped[str] = mapped_column(String(5))
    path: Mapped[str] = mapped_column(String(1000))
    ats_score: Mapped[int | None] = mapped_column(Integer)
    ats_report: Mapped[dict] = mapped_column(JSON, default=dict)
    truth_check: Mapped[dict] = mapped_column(JSON, default=dict)
    change_log: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="ready")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    posting: Mapped[Posting] = relationship(back_populates="documents")


class Draft(Base):
    __tablename__ = "drafts"
    id: Mapped[int] = mapped_column(primary_key=True)
    posting_id: Mapped[int] = mapped_column(ForeignKey("postings.id"), index=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"))
    kind: Mapped[str] = mapped_column(String(20))
    subject: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(5), default="en")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    posting: Mapped[Posting] = relationship(back_populates="drafts")
    contact: Mapped[Contact | None] = relationship(back_populates="drafts")


class Pipeline(Base):
    __tablename__ = "pipeline"
    id: Mapped[int] = mapped_column(primary_key=True)
    posting_id: Mapped[int] = mapped_column(ForeignKey("postings.id"), unique=True)
    stage: Mapped[str] = mapped_column(String(20), default="new", index=True)
    close_reason: Mapped[str | None] = mapped_column(String(20))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime)
    notes: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    posting: Mapped[Posting] = relationship(back_populates="pipeline")


class Activity(Base):
    __tablename__ = "activities"
    id: Mapped[int] = mapped_column(primary_key=True)
    posting_id: Mapped[int] = mapped_column(ForeignKey("postings.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    outcome: Mapped[str | None] = mapped_column(String(60))
    body: Mapped[str] = mapped_column(Text, default="")
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    posting: Mapped[Posting] = relationship(back_populates="activities")


class AdapterHealth(Base):
    __tablename__ = "adapter_health"
    adapter: Mapped[str] = mapped_column(String(40), primary_key=True)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime)
    calls_today: Mapped[int] = mapped_column(Integer, default=0)
    calls_date: Mapped[str | None] = mapped_column(String(10))


class SpendLedger(Base):
    __tablename__ = "spend_ledger"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    credits: Mapped[float] = mapped_column(Float, default=0.0)
    usd_estimate: Mapped[float] = mapped_column(Float, default=0.0)
    posting_id: Mapped[int | None] = mapped_column(ForeignKey("postings.id"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="running")
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
