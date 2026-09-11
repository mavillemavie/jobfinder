from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from jobfinder.config import LocationEntry, Profile
from jobfinder.db.models import Posting
from jobfinder.discovery.normalize import _ascii

_STOP = {"of", "and", "&", "the", "-", "/", "de", "des", "en", "d"}


@dataclass
class PrefilterResult:
    passed: bool
    reason: str
    matched_term: str | None = None
    overlap: int = 0
    location_key: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _tokens(text: str) -> set[str]:
    # Accents are folded first: normalize_title() already ASCII-folds every stored title,
    # so a config term written properly ("analyste de données") must fold the same way or
    # "données" would split into the nonsense tokens {donn, es} and never match.
    return {t for t in re.split(r"[^a-z0-9]+", _ascii(text).lower()) if t and t not in _STOP}


def title_matches(normalized_title: str, terms: list[str]) -> str | None:
    title_tokens = _tokens(normalized_title)
    for term in sorted(terms, key=len, reverse=True):
        if _tokens(term) <= title_tokens:
            return term
    return None


def location_matches(posting: Posting, entries: list[LocationEntry]) -> str | None:
    # Check entries by specificity (city > country > remote), not by config list order,
    # so a specific city entry wins over a broader country-wide entry listed earlier.
    for entry in entries:
        if entry.cities:
            cities = {c.lower() for c in entry.cities}
            if posting.country == entry.country and (posting.city or "").lower() in cities:
                return entry.key
    for entry in entries:
        if not entry.cities and entry.country:
            if posting.country == entry.country:
                return entry.key
    for entry in entries:
        if not entry.cities and not entry.country and entry.remote_scope:
            if posting.remote_type == "remote" and (
                posting.remote_scope is None or posting.remote_scope in entry.remote_scope
            ):
                return entry.key
    return None


def keyword_overlap(text: str, skill_terms: set[str]) -> int:
    low = text.lower()
    return sum(
        1
        for term in skill_terms
        if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", low)
    )


def prefilter(
    posting: Posting, profile: Profile, skill_terms: set[str], *, check_overlap: bool = True
) -> PrefilterResult:
    term = title_matches(posting.normalized_title, profile.title_terms())
    if term is None:
        return PrefilterResult(False, "wrong_title")
    title_low = posting.title.lower()
    if any(w.lower() in title_low for w in profile.titles.exclude_words):
        return PrefilterResult(False, "excluded_word", term)
    if (posting.seniority or "unspecified") not in profile.titles.seniority_allowed:
        return PrefilterResult(False, "seniority", term)
    loc_key = location_matches(posting, profile.enabled_locations())
    if loc_key is None:
        return PrefilterResult(False, "wrong_location", term)
    overlap = keyword_overlap(posting.description_text or "", skill_terms)
    if check_overlap and skill_terms and posting.description_complete:
        if overlap < profile.scoring.min_keyword_overlap:
            return PrefilterResult(False, "low_overlap", term, overlap, loc_key)
    return PrefilterResult(True, "ok", term, overlap, loc_key)
