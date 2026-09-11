from __future__ import annotations

import re

from jobfinder.contacts.base import ContactContext, Person, StepResult
from jobfinder.contacts.providers.phone import normalize_phone, phones_in_text
from jobfinder.discovery.normalize import detect_ats
from jobfinder.llm.base import load_prompt, load_schema

_EMAIL = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$")
MAX_TEXT = 6000


def _clean_email(raw: str | None) -> str | None:
    e = (raw or "").strip().lower()
    return e if _EMAIL.match(e) else None


def _add_phone(ctx: ContactContext, number: str | None, kind: str, source: str) -> None:
    if not number:
        return
    if any(n == number for n, _, _ in ctx.phones) or any(p.phone == number for p in ctx.people):
        return
    ctx.phones.append((number, kind, source))


class PostingExtractStep:
    name = "posting_extract"

    def run(self, ctx: ContactContext) -> StepResult:
        notes: dict = {}
        ats = detect_ats(ctx.posting.apply_url)
        if ats:
            notes["ats"] = {"type": ats[0], "token": ats[1]}
            if not ctx.company.ats_type:
                ctx.company.ats_type, ctx.company.ats_board_token = ats
        text = (ctx.posting.description_text or "")[:MAX_TEXT]
        user = (
            f"TITLE: {ctx.posting.title}\nCOMPANY: {ctx.company.name}\n"
            f"APPLY_URL: {ctx.posting.apply_url}\n\nPOSTING\n{text}"
        )
        data = ctx.llm.complete_json(
            task="contact_extract", system=load_prompt("contact_extract"), user=user,
            schema=load_schema("contact_extract"), tier="fast", language=ctx.language,
        )
        added = 0
        for raw in data.get("people", []):
            name = (raw.get("full_name") or "").strip()
            if not name:
                continue
            hint = raw.get("role_hint")
            person = Person(
                full_name=name, title=(raw.get("title") or "").strip() or None,
                role_kind=hint if hint in ("hiring_manager", "recruiter") else "other",
                sources=["posting"], title_relevance=0.9,
            )
            email = _clean_email(raw.get("email"))
            if email:
                person.email, person.email_status = email, "verified"
                person.email_source = "posting"
            phone = normalize_phone(raw.get("phone"), ctx.country)
            if phone:
                person.phone, person.phone_kind, person.phone_source = phone, "direct", "posting"
            person.evidence["posting_extract"] = {"email": email, "phone": phone}
            ctx.people.append(person)
            added += 1
        for raw in data.get("emails", []):
            email = _clean_email(raw)
            if email and email not in ctx.stated_emails and not any(
                p.email == email for p in ctx.people
            ):
                ctx.stated_emails.append(email)
        for raw in data.get("phones", []):
            _add_phone(ctx, normalize_phone(raw, ctx.country), "switchboard", "posting")
        for number in phones_in_text(text, ctx.country):
            _add_phone(ctx, number, "switchboard", "posting")
        notes.update(
            {"people": added, "emails": len(ctx.stated_emails), "phones": len(ctx.phones),
             "llm_notes": (data.get("notes") or "")[:300]}
        )
        return StepResult(self.name, notes=notes)
