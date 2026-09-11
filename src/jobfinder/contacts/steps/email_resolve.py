from __future__ import annotations

import re

from jobfinder.contacts.base import ContactContext, Person, StepResult, strip_diacritics
from jobfinder.discovery.base import SourceError

PATTERNS = ("{first}.{last}", "{f}{last}", "{first}", "{first}{last}")
MAX_CANDIDATES = 4  # 4 × 0.5 credit = the whole per-job Hunter cap


def _local(s: str) -> str:
    return re.sub(r"[^a-z0-9-]", "", strip_diacritics(s or "").lower())


def apply_pattern(pattern: str, first: str, last: str, domain: str) -> str | None:
    f, last_ = _local(first), _local(last)
    if not f or not domain:
        return None
    if not last_ and ("{last}" in pattern or "{l}" in pattern):
        return None
    local = (
        pattern.replace("{first}", f).replace("{last}", last_)
        .replace("{f}", f[:1]).replace("{l}", last_[:1])
    )
    if not local or "{" in local or local.startswith(".") or local.endswith("."):
        return None
    return f"{local}@{domain}"


def candidates(first: str, last: str, domain: str, pattern: str | None = None) -> list[str]:
    """Observed pattern when Hunter gave one, else the playbook's 4 defaults; an accented form
    competes for slot 4 only."""
    out: list[str] = []
    for p in ([pattern] if pattern else list(PATTERNS)):
        e = apply_pattern(p, first, last, domain)
        if e and e not in out:
            out.append(e)
    if not pattern and last and strip_diacritics(first + last) != first + last:
        accented = re.sub(r"[^\w.-]", "", f"{first}.{last}".lower()) + f"@{domain}"
        if accented not in out:
            out = out[:MAX_CANDIDATES - 1] + [accented]
    return out[:MAX_CANDIDATES]


def _set_email(person: Person, email: str, status: str, source: str) -> None:
    person.email, person.email_status, person.email_source = email, status, source


def _verify(ctx: ContactContext, person: Person, email: str, credits: dict, notes: dict) -> str:
    """One Hunter verification (0.5 credit). Returns the outcome keyword."""
    ok, why = ctx.budget.can_spend("hunter", 0.5)
    if not ok:
        notes["stopped"] = why
        return "stopped"
    try:
        v = ctx.hunter.email_verifier(email)
    except SourceError as exc:
        notes.setdefault("errors", []).append(str(exc)[:160])
        return "error"
    ctx.budget.spend("hunter", 0.5)
    credits["hunter"] = credits.get("hunter", 0.0) + 0.5
    person.evidence.setdefault("verifier", []).append({
        "email": email, "status": v.status, "result": v.result, "score": v.score,
        "accept_all": v.accept_all, "smtp_check": v.smtp_check,
    })
    if v.status == "accept_all" or v.accept_all:
        ctx.company.catch_all = True
        _set_email(person, email, "catch_all", "hunter_verifier")
        notes["catch_all"] += 1
        return "catch_all"
    if v.status == "valid" and v.smtp_check:
        _set_email(person, email, "verified", "hunter_verifier")
        notes["verified"] += 1
        return "verified"
    if v.status == "valid":
        # Hunter says valid but did not confirm over SMTP: keep it, capped as a guess (playbook).
        _set_email(person, email, "guessed", "hunter_verifier")
        notes["guessed"] += 1
        return "valid_unconfirmed"
    if v.status in ("webmail", "disposable"):
        return "discard"
    if v.status == "unknown":
        if not person.email:
            _set_email(person, email, "guessed", "hunter_verifier")
            notes["guessed"] += 1
        return "unknown"
    notes["invalid"] += 1
    return "invalid"


def _resolvable(p: Person) -> bool:
    return bool(p.full_name and p.first_name and not p.evidence.get("last_name_obfuscated")
                and not p.email)


class EmailResolveStep:
    name = "email_resolve"

    def run(self, ctx: ContactContext) -> StepResult:
        if not ctx.domain:
            return StepResult(self.name, skipped="no domain")
        people = [p for p in sorted(ctx.people, key=lambda p: -p.title_relevance) if _resolvable(p)]
        if not people:
            return StepResult(self.name, skipped="no resolvable person")
        credits: dict[str, float] = {}
        notes: dict = {"verified": 0, "catch_all": 0, "guessed": 0, "invalid": 0, "apollo_match": 0}
        domain, pattern = ctx.domain, ctx.company.email_pattern
        for person in people:
            if ctx.has_verified_email():
                break  # playbook: stop on the first valid
            cands = candidates(person.first_name, person.last_name, domain, pattern)
            if ctx.company.catch_all:
                if cands:
                    _set_email(person, cands[0], "catch_all", "pattern")
                    notes["catch_all"] += 1
                continue
            outcome = "none"
            if ctx.hunter is not None:
                for cand in cands:
                    outcome = _verify(ctx, person, cand, credits, notes)
                    if outcome not in ("unknown", "invalid"):
                        break
            if outcome == "stopped":
                break  # the job's Hunter budget is spent: nobody else gets verified
            if person.email and person.email_status == "verified":
                continue
            # (c) finder — only when no observed pattern exists and a full credit remains
            if (
                ctx.hunter is not None and not pattern and person.email_status != "catch_all"
                and outcome != "error" and ctx.budget.can_spend("hunter", 1.0)[0]
            ):
                try:
                    f = ctx.hunter.email_finder(domain, person.first_name, person.last_name)
                except SourceError as exc:
                    notes.setdefault("errors", []).append(str(exc)[:160])
                else:
                    ctx.budget.spend("hunter", 1.0)
                    credits["hunter"] = credits.get("hunter", 0.0) + 1.0
                    person.evidence["finder"] = {
                        "email": f.email, "score": f.score, "status": f.status
                    }
                    if f.email:
                        status = {"valid": "verified", "accept_all": "catch_all"}.get(
                            f.status or "", "guessed"
                        )
                        _set_email(person, f.email, status, "hunter_finder")
                        notes[status] += 1
                        if status == "catch_all":
                            ctx.company.catch_all = True
                        if status == "verified":
                            continue
            # (d) Apollo enrichment — only for an Apollo-flagged has_email person still unresolved
            if ctx.apollo is not None and person.has_email and person.email_status != "verified":
                ok, why = ctx.budget.can_spend("apollo", 1.0)
                if not ok:
                    notes["apollo_stopped"] = why
                    continue
                try:
                    m = ctx.apollo.people_match(person.first_name, person.last_name, domain)
                except SourceError as exc:
                    notes.setdefault("errors", []).append(str(exc)[:160])
                    continue
                ctx.budget.spend("apollo", 1.0)
                credits["apollo"] = credits.get("apollo", 0.0) + 1.0
                notes["apollo_match"] += 1
                if m and m.email:
                    person.evidence["apollo_match"] = {"email": m.email, "title": m.title}
                    verdict = "none"
                    if ctx.hunter is not None and not ctx.company.catch_all:
                        verdict = _verify(ctx, person, m.email, credits, notes)
                    if verdict not in ("verified", "catch_all", "valid_unconfirmed"):
                        _set_email(person, m.email, "unverified", "apollo")
        return StepResult(self.name, credits=credits, notes=notes)
