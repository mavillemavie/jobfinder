from __future__ import annotations

from jobfinder.contacts.base import Person


def email_confidence(status: str | None, source: str | None) -> float:
    """Spec §11 base weights: stated in posting 0.95; provider-verified 0.85; provider-found but
    unverified (Apollo) 0.6; catch-all 0.4; guess 0.25."""
    if status == "verified":
        return 0.95 if source == "posting" else 0.85
    if status == "unverified" and source == "apollo":
        return 0.6
    if status == "catch_all":
        return 0.4
    if status == "guessed":
        return 0.25
    return 0.0


def phone_confidence(kind: str | None, source: str | None) -> float:
    if not kind:
        return 0.0
    if source == "posting":
        return 0.95
    if kind == "switchboard":
        return 0.9
    return 0.8


def person_confidence(person: Person) -> float:
    """Base weight (best of email / direct phone; 0.3 for a bare name) scaled by title relevance."""
    base = 0.0
    if person.email:
        base = email_confidence(person.email_status, person.email_source)
    if person.phone and person.phone_kind == "direct":
        base = max(base, phone_confidence(person.phone_kind, person.phone_source))
    if base == 0.0 and person.full_name:
        base = 0.3
    return round(base * (0.6 + 0.4 * person.title_relevance), 3)
