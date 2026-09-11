from __future__ import annotations

from jobfinder.contacts.base import ContactContext, StepResult
from jobfinder.db.models import utcnow
from jobfinder.discovery.base import SourceError


class EmailPatternStep:
    name = "email_pattern"

    def run(self, ctx: ContactContext) -> StepResult:
        co = ctx.company
        if not ctx.domain:
            return StepResult(self.name, skipped="no domain")
        if co.email_pattern or co.catch_all:
            return StepResult(
                self.name, skipped="cached",
                notes={"pattern": co.email_pattern, "catch_all": co.catch_all},
            )
        if any(p.email for p in ctx.people):
            return StepResult(self.name, skipped="email already known")
        # Playbook rationing rule (a): spend a Hunter credit only for a fully named person.
        if not any(
            p.full_name and p.last_name and not p.evidence.get("last_name_obfuscated")
            for p in ctx.people
        ):
            return StepResult(self.name, skipped="no named person")
        if ctx.hunter is None:
            return StepResult(self.name, skipped="no hunter key")
        ok, why = ctx.budget.can_spend("hunter", 1.0)
        if not ok:
            return StepResult(self.name, skipped=why)
        try:
            d = ctx.hunter.domain_search(ctx.domain, limit=1)
        except SourceError as exc:
            return StepResult(self.name, ok=False, error=str(exc)[:300])
        ctx.budget.spend("hunter", 1.0)
        co.email_pattern = d.pattern
        co.catch_all = d.accept_all
        co.pattern_checked_at = utcnow().replace(tzinfo=None)
        if d.sample_email:
            ctx.notes["hunter_sample_email"] = d.sample_email
        return StepResult(
            self.name, credits={"hunter": 1.0},
            notes={
                "pattern": d.pattern, "accept_all": d.accept_all, "organization": d.organization,
            },
        )
