from __future__ import annotations

from jobfinder.contacts.base import ContactContext, Person, StepResult
from jobfinder.contacts.confidence import email_confidence, person_confidence, phone_confidence
from jobfinder.db.models import Contact

MAX_ROWS = 3
GENERIC_LOCAL = (
    "info", "contact", "careers", "career", "jobs", "job", "hr", "rh", "recruiting", "recruitment",
    "recrutement", "talent", "emploi", "emplois", "candidature", "candidatures", "hello", "admin",
    "office", "sales", "support", "cv", "resume", "apply", "hiring",
)


def is_generic_email(email: str) -> bool:
    local = email.split("@")[0].lower()
    return any(
        local == g or local.startswith(g + ".") or local.startswith(g + "-") for g in GENERIC_LOCAL
    )


def _row(ctx: ContactContext, p: Person) -> Contact:
    evidence = {
        **p.evidence, "sources": p.sources, "title_relevance": p.title_relevance,
        "email_confidence": email_confidence(p.email_status, p.email_source) if p.email else 0.0,
        "phone_confidence": phone_confidence(p.phone_kind, p.phone_source) if p.phone else 0.0,
        "has_email_signal": p.has_email, "has_direct_phone_signal": p.has_direct_phone,
    }
    return Contact(
        posting_id=ctx.posting.id, company_id=ctx.company.id, full_name=(p.full_name or "")[:200],
        title=(p.title or None), role_kind=p.role_kind, email=p.email,
        email_status=p.email_status if p.email else "unverified", email_source=p.email_source,
        phone=p.phone, phone_kind=p.phone_kind, phone_source=p.phone_source,
        linkedin_url=p.linkedin_url, confidence=person_confidence(p), evidence=evidence,
    )


class AssembleStep:
    name = "assemble"

    def run(self, ctx: ContactContext) -> StepResult:
        session, posting = ctx.session, ctx.posting
        posting.contacts.clear()  # delete-orphan cascade drops the previous rows on flush
        session.flush()
        switchboard = next(((n, s) for n, k, s in ctx.phones if k == "switchboard"), None)
        named = [p for p in ctx.people if p.full_name]
        for p in named:
            if not p.phone and switchboard:
                p.phone, p.phone_kind = switchboard[0], "switchboard"
                p.phone_source = switchboard[1]
        named.sort(key=lambda p: -person_confidence(p))
        rows = [_row(ctx, p) for p in named[:MAX_ROWS]]
        outcome = "people"
        if not rows:
            generic = next((e for e in ctx.stated_emails if is_generic_email(e)), None) or next(
                iter(ctx.stated_emails), None
            )
            if switchboard or generic:
                outcome = "switchboard_only"
                rows.append(Contact(
                    posting_id=posting.id, company_id=ctx.company.id, full_name=None, title=None,
                    role_kind="other", email=generic,
                    email_status="verified" if generic else "unverified",
                    email_source="posting" if generic else None,
                    phone=switchboard[0] if switchboard else None,
                    phone_kind="switchboard" if switchboard else None,
                    phone_source=switchboard[1] if switchboard else None,
                    confidence=0.9 if switchboard else 0.5,
                    evidence={"switchboard_only": True, "person_confidence": 0.0,
                              "phone_confidence": 0.9 if switchboard else 0.0,
                              "website": ctx.website, "domain": ctx.domain},
                ))
            else:
                outcome = "needs_manual"
                rows.append(Contact(
                    posting_id=posting.id, company_id=ctx.company.id, full_name=None, title=None,
                    role_kind="other", confidence=0.0,
                    evidence={"needs_manual": True, "reason": "no person, no phone",
                              "website": ctx.website, "domain": ctx.domain, "titles": ctx.titles},
                ))
        for r in rows:
            posting.contacts.append(r)  # keeps the loaded collection in sync for callers
        session.flush()
        return StepResult(self.name, notes={
            "rows": len(rows), "outcome": outcome,
            "verified_email": ctx.has_verified_email(), "any_phone": ctx.has_any_phone(),
        })
