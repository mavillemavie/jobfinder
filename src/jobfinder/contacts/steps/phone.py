from __future__ import annotations

from jobfinder.contacts.base import ContactContext, StepResult
from jobfinder.contacts.providers.phone import (
    fetch_contact_page_phone,
    normalize_phone,
    phones_in_text,
)
from jobfinder.discovery.fetch import BudgetExceeded, RateLimited


def _kg_phone(kg: dict, country: str) -> str | None:
    attrs = kg.get("attributes") or {}
    raw = kg.get("phone") or attrs.get("Phone") or attrs.get("Téléphone") or attrs.get("Telephone")
    return normalize_phone(str(raw), country) if raw else None


class PhoneStep:
    name = "phone"

    def run(self, ctx: ContactContext) -> StepResult:
        co = ctx.company
        credits: dict[str, float] = {}
        notes: dict = {}
        if co.main_phone and not ctx.has_any_phone():
            ctx.phones.append((co.main_phone, "switchboard", "company_cache"))
            return StepResult(self.name, notes={"how": "cached", "phones": ctx.phones})
        if ctx.has_any_phone():
            return StepResult(self.name, notes={"how": "already_found", "phones": ctx.phones})
        how = "none"
        number = _kg_phone(ctx.knowledge_graph or {}, ctx.country)
        if number:
            ctx.phones.append((number, "switchboard", "serper_kg"))
            how = "knowledge_graph"
        if not number and ctx.website:
            try:
                found = fetch_contact_page_phone(ctx.http, ctx.website, ctx.country)
            except (RateLimited, BudgetExceeded) as exc:
                notes["contact_page_error"] = str(exc)[:200]
                found = None
            if found:
                number = found[0]
                ctx.phones.append((number, "switchboard", found[1]))
                how = "contact_page"
        if not number and ctx.search is not None:
            gl, hl = ctx.gl_hl
            q = f'"{co.name}" contact phone' if hl == "en" else f'"{co.name}" téléphone contact'
            res = ctx.search.search(q, gl=gl, hl=hl, num=5)
            if res is None:
                notes["search_skipped"] = ctx.search.last_reason
            else:
                credits["websearch"] = 1.0
                number = _kg_phone(res.knowledge_graph or {}, ctx.country)
                if not number:
                    text = " ".join(f"{h.title} {h.snippet}" for h in res.hits)
                    hits = phones_in_text(text, ctx.country)
                    number = hits[0] if hits else None
                if number:
                    ctx.phones.append((number, "switchboard", "search_snippet"))
                    how = "search_snippet"
        if number and not co.main_phone:
            co.main_phone = number
        return StepResult(
            self.name, credits=credits, notes={**notes, "how": how, "phones": ctx.phones}
        )
