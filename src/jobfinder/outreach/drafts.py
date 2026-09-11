from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from jobfinder.config import Profile
from jobfinder.db.models import Contact, Draft, Posting
from jobfinder.llm.base import LLMProvider, load_prompt, load_schema
from jobfinder.scoring.resume_summary import build_resume_summary
from jobfinder.settings import Settings
from jobfinder.tailoring.master_schema import load_master, master_exists

MAX_POSTING_CHARS = 4000


def load_voice_notes(settings: Settings) -> str:
    path = Path(settings.voice_file).expanduser()
    return path.read_text(encoding="utf-8")[:3000] if path.exists() else ""


def pick_contact(posting: Posting, contact_id: int | None) -> Contact | None:
    if contact_id is not None:
        return next((c for c in posting.contacts if c.id == contact_id), None)
    ranked = sorted(
        posting.contacts, key=lambda c: (c.role_kind != "hiring_manager", -c.confidence)
    )
    return ranked[0] if ranked else None


def _candidate_block() -> tuple[str, str, str]:
    """(name, phone, resume summary) — empty strings when no master has been ingested yet."""
    if not master_exists():
        return "", "", "(no master resume ingested yet — run `jobfinder ingest`)"
    master = load_master()
    return master.contact.name, master.contact.phone or "", build_resume_summary(master)


def draft_for_posting(
    session: Session,
    posting_id: int,
    *,
    llm: LLMProvider,
    profile: Profile,
    contact_id: int | None = None,
    voice_notes: str = "",
) -> list[Draft]:
    posting = session.get(Posting, posting_id)
    if posting is None:
        raise ValueError(f"no posting {posting_id}")
    name, phone, summary = _candidate_block()
    contact = pick_contact(posting, contact_id)
    score = posting.latest_score
    contact_block = (
        f"CONTACT: {contact.full_name or 'unknown'} — {contact.title or ''} ({contact.role_kind})"
        if contact else "CONTACT: unknown (address generically to the hiring team)"
    )
    user = (
        f"LANGUAGE: {posting.language}\nCANDIDATE NAME: {name}\nCANDIDATE PHONE: {phone}\n"
        f"VOICE NOTES:\n{voice_notes or 'direct, concise, no fluff'}\n\n"
        f"RESUME SUMMARY:\n{summary}\n\n"
        f"POSTING: {posting.title} at {posting.company.name}\n"
        f"FIT REASONS: {'; '.join(score.reasons) if score else ''}\n"
        f"{contact_block}\n\nPOSTING TEXT:\n{posting.description_text[:MAX_POSTING_CHARS]}"
    )
    data = llm.complete_json(
        task="outreach_drafts", system=load_prompt("outreach_drafts"), user=user,
        schema=load_schema("outreach_drafts"), tier="strong", language=posting.language,
    )
    cid = contact.id if contact else None
    posting.drafts.clear()  # delete-orphan cascade drops the previous drafts on flush
    drafts = [
        Draft(
            posting_id=posting.id, contact_id=cid, kind="email", subject=data["email_subject"],
            body=data["email_body"], language=posting.language,
        ),
        Draft(
            posting_id=posting.id, contact_id=cid, kind="call_script", subject=None,
            body=data["call_script"], language=posting.language,
        ),
    ]
    posting.drafts.extend(drafts)
    session.commit()
    return drafts
