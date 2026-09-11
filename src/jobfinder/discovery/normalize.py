from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

SENIORITY_MAP: dict[str, str] = {
    "junior": "junior", "jr": "junior",
    "intermediate": "intermediate",
    "senior": "senior", "sr": "senior",
    "lead": "lead", "principal": "lead", "staff": "lead",
    "manager": "management", "director": "management", "head": "management",
    "chief": "management", "vp": "management", "directeur": "management",
    "directrice": "management",
    "intern": "intern", "internship": "intern", "co-op": "intern", "coop": "intern",
    "student": "intern", "stagiaire": "intern",
}
_LEVEL_TOKENS = {"i", "ii", "iii", "iv", "v", "1", "2", "3", "4", "5"}
_FILLER = {
    "remote", "hybrid", "hybride", "contract", "contractual", "temporary", "permanent", "full",
    "time", "part", "bilingual", "bilingue", "f/h", "h/f", "m/f", "urgent", "new", "job",
    "position",
}
_LEGAL_SUFFIXES = {
    "inc", "ltd", "ltee", "ltée", "llc", "corp", "corporation", "co", "company", "limited",
    "plc", "sa", "s.a", "gmbh", "group", "groupe", "the",
}
_PROVINCES: dict[str, str] = {
    "ab": "AB", "alberta": "AB", "bc": "BC", "british columbia": "BC", "colombie-britannique": "BC",
    "mb": "MB", "manitoba": "MB", "nb": "NB", "new brunswick": "NB", "nl": "NL", "ns": "NS",
    "nova scotia": "NS", "on": "ON", "ontario": "ON", "pe": "PE", "qc": "QC", "quebec": "QC",
    "québec": "QC", "sk": "SK", "saskatchewan": "SK",
}
_CA_CITIES = {
    "montreal", "montréal", "laval", "longueuil", "quebec city", "toronto", "ottawa", "vancouver",
    "burnaby", "richmond", "surrey", "edmonton", "calgary", "winnipeg", "halifax", "victoria",
    "mississauga", "gatineau",
}
_UK_MARKERS = {
    "united kingdom", "uk", "england", "scotland", "wales", "northern ireland", "london",
    "manchester", "edinburgh", "birmingham", "leeds", "glasgow", "bristol", "cambridge", "oxford",
}
_ATS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)\.greenhouse\.io/([A-Za-z0-9_-]+)")),
    ("lever", re.compile(r"jobs\.lever\.co/([A-Za-z0-9_-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_-]+)")),
    ("workable", re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)")),
    ("smartrecruiters", re.compile(r"jobs\.smartrecruiters\.com/([A-Za-z0-9_-]+)")),
]
_FR_STOP = {
    "le", "la", "les", "des", "et", "pour", "avec", "nous", "vous", "une", "dans", "sur", "est",
    "du", "au",
}
_EN_STOP = {
    "the", "and", "for", "with", "you", "our", "that", "this", "will", "are", "to", "of", "in",
}


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9/'\-]+", text.lower()) if t]


def normalize_title(title: str) -> tuple[str, str]:
    seniority = "unspecified"
    kept: list[str] = []
    for tok in _tokens(_ascii(title)):
        tok = tok.strip("'-.")
        if not tok:
            continue
        if tok in SENIORITY_MAP:
            if seniority == "unspecified":
                seniority = SENIORITY_MAP[tok]
            continue
        if tok in _LEVEL_TOKENS or tok in _FILLER:
            continue
        kept.extend(t for t in tok.replace("/", " ").replace("'", " ").split() if t)
    return " ".join(kept), seniority


def normalize_company(name: str) -> str:
    toks = [t.strip(",") for t in _tokens(_ascii(name).replace(".", ""))]
    toks = [t for t in toks if t and t not in _LEGAL_SUFFIXES]
    return " ".join(toks)


@dataclass
class LocationInfo:
    country: str | None
    region: str | None
    city: str | None
    remote_type: str
    remote_scope: str | None


def parse_location(
    raw: str | None, remote_hint: str | None = None, country_hint: str | None = None
) -> LocationInfo:
    text = (raw or "").strip()
    low = _ascii(text).lower()
    remote_type = remote_hint or (
        "remote" if ("remote" in low or "teletravail" in low or "télétravail" in text.lower())
        else "hybrid" if ("hybrid" in low or "hybride" in low)
        else "unknown"
    )
    parts = [p.strip() for p in re.split(r"[,\-–|/()]+", text) if p.strip()]
    country = country_hint
    region = None
    city = None
    for part in parts:
        pl = _ascii(part).lower()
        if pl in ("canada",):
            country = country or "CA"
        elif pl in _UK_MARKERS:
            country = country or "GB"
            if pl not in (
                "united kingdom", "uk", "england", "scotland", "wales", "northern ireland",
            ):
                city = city or part
        elif pl in _PROVINCES:
            region = region or _PROVINCES[pl]
            country = country or "CA"
        elif pl in _CA_CITIES:
            city = city or part
            country = country or "CA"
        elif pl in ("remote", "hybrid", "hybride", "teletravail", "anywhere", "worldwide"):
            continue
        elif city is None and len(pl) > 2 and not pl.startswith("anywhere"):
            city = part
    if city:
        city = _ascii(city).title() if _ascii(city).lower() in _CA_CITIES else city
    remote_scope: str | None = None
    if "worldwide" in low or "anywhere" in low or "global" in low:
        remote_scope = "worldwide"
        if country is None:
            city = None
    elif remote_type == "remote" and country == "CA":
        remote_scope = "canada"
    elif country == "GB":
        remote_scope = "uk"
    elif remote_type == "remote" and ("usa" in low or "united states" in low or "us only" in low):
        remote_scope = "us"
    return LocationInfo(country, region, city, remote_type, remote_scope)


def detect_language(text: str) -> str:
    toks = set(_tokens(text[:2000]))
    fr = len(toks & _FR_STOP)
    en = len(toks & _EN_STOP)
    return "fr" if fr > en * 1.2 else "en"


def detect_ats(url: str | None) -> tuple[str, str] | None:
    if not url:
        return None
    for ats, pattern in _ATS_PATTERNS:
        m = pattern.search(url)
        if m:
            return ats, m.group(1)
    return None


def slugify(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", _ascii(text).lower())).strip("-")
