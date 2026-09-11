from __future__ import annotations

import re

from jobfinder.contacts.base import ContactContext, Person, StepResult, role_kind_for_title
from jobfinder.discovery.base import SourceError
from jobfinder.discovery.normalize import normalize_company

MAX_QUERIES = 3       # spec §11 step 4's own allowance
MAX_PEOPLE = 3        # stop searching once this many fully named people are known
MAX_PEOPLE_KEPT = 5   # hard cap on ctx.people
_SPLIT = re.compile(r"\s+[-–—]\s+")
_SUFFIX = re.compile(r"\s*[|\-–—]\s*LinkedIn\s*$", re.I)
_AT_COMPANY = re.compile(
    r"^(?P<title>.+?)\s+(?:at|@|chez)\s+(?P<company>[^|]+?)\s*(?:\|.*)?$", re.I
)
_QUERY_PUNCT = re.compile(r"[^\w\s&/-]", re.UNICODE)


def parse_linkedin_hit(title: str) -> tuple[str, str, str | None] | None:
    """Google renders LinkedIn profiles as `Name - Title at Company - LinkedIn`,
    `Name - Title | Specialty - LinkedIn`, or the older `Name - Title - Company | LinkedIn`;
    the suffix may be missing when Google truncates. Returns (name, title, company | None)."""
    head = _SUFFIX.sub("", title or "").strip()
    parts = [p.strip() for p in _SPLIT.split(head) if p.strip()]
    if len(parts) < 2:
        return None
    name, rest = parts[0], parts[1:]
    m = _AT_COMPANY.match(rest[0])
    if m:
        return name, m.group("title").strip(), m.group("company").strip()
    if len(rest) == 1:
        return name, rest[0], None
    return name, rest[0], " - ".join(rest[1:])


def linkedin_query(company: str, title: str) -> str:
    """Company quoted (must match), title as loose words — quoted titles over-constrain Google."""
    words = _QUERY_PUNCT.sub(" ", title).split()
    return f'site:linkedin.com/in "{company}" ' + " ".join(words)


def title_relevance(title: str | None, wanted: list[str]) -> float:
    t = (title or "").lower().strip()
    if not t:
        return 0.3
    for w in wanted:
        wl = w.lower().strip()
        if wl and (wl in t or t in wl):
            return 1.0
    kind = role_kind_for_title(title)
    return 0.7 if kind == "hiring_manager" else 0.6 if kind == "recruiter" else 0.3


def _initial(person: Person) -> str:
    return (person.last_name[:1] or "").lower()


def merge_person(ctx: ContactContext, new: Person) -> None:
    """Merge by first name + last initial so Apollo's 'Dana L.' becomes 'Dana Lee' once a
    LinkedIn hit supplies the full name; otherwise append (capped)."""
    for p in ctx.people:
        if p.first_name.lower() != new.first_name.lower() or _initial(p) != _initial(new):
            continue
        if p.evidence.get("last_name_obfuscated") and not new.evidence.get("last_name_obfuscated"):
            p.full_name, p.first_name, p.last_name = new.full_name, new.first_name, new.last_name
            p.evidence.pop("last_name_obfuscated", None)
        p.title = p.title or new.title
        p.linkedin_url = p.linkedin_url or new.linkedin_url
        if p.has_email is None:
            p.has_email = new.has_email
        p.has_direct_phone = p.has_direct_phone or new.has_direct_phone
        p.sources = list(dict.fromkeys(p.sources + new.sources))
        p.evidence.update({k: v for k, v in new.evidence.items() if k != "last_name_obfuscated"})
        p.title_relevance = max(p.title_relevance, new.title_relevance)
        if p.role_kind == "other" and new.role_kind != "other":
            p.role_kind = new.role_kind
        return
    if len(ctx.people) < MAX_PEOPLE_KEPT:
        ctx.people.append(new)


def _named(ctx: ContactContext) -> int:
    return sum(
        1 for p in ctx.people if p.full_name and not p.evidence.get("last_name_obfuscated")
    )


class PeopleSearchStep:
    name = "people_search"

    def run(self, ctx: ContactContext) -> StepResult:
        if not ctx.titles:
            return StepResult(self.name, skipped="no target titles")
        notes: dict = {}
        credits: dict[str, float] = {}
        if ctx.apollo is not None and ctx.domain:
            try:
                found = ctx.apollo.people_search(ctx.domain, ctx.titles[:5], per_page=5)
            except SourceError as exc:
                notes["apollo_error"] = str(exc)[:200]
                found = []
            notes["apollo"] = len(found)
            for ap in found:
                if not ap.first_name:
                    continue
                person = Person(
                    full_name=f"{ap.first_name} {ap.last_name_obfuscated}".strip(),
                    title=ap.title or None, has_email=ap.has_email,
                    has_direct_phone=ap.has_direct_phone, sources=["apollo"],
                    title_relevance=title_relevance(ap.title, ctx.titles),
                )
                person.evidence["apollo"] = {
                    "id": ap.id, "has_email": ap.has_email, "has_direct_phone": ap.has_direct_phone,
                }
                person.evidence["last_name_obfuscated"] = True
                merge_person(ctx, person)
        if ctx.search is not None:
            company = ctx.company.name
            target = normalize_company(company)
            gl, hl = ctx.gl_hl
            queries = 0
            for t in ctx.titles:
                if queries >= MAX_QUERIES or _named(ctx) >= MAX_PEOPLE:
                    break
                res = ctx.search.search(linkedin_query(company, t), gl=gl, hl=hl, num=10)
                if res is None:
                    notes["search_stopped"] = ctx.search.last_reason
                    break
                queries += 1
                for hit in res.hits:
                    if "linkedin.com/in/" not in hit.link:
                        continue
                    parsed = parse_linkedin_hit(hit.title)
                    if not parsed:
                        continue
                    name, title, comp = parsed
                    if comp is not None:
                        nc = normalize_company(comp)
                        if nc != target and target not in nc:
                            continue
                    elif target not in normalize_company(hit.snippet or ""):
                        continue
                    person = Person(
                        full_name=name, title=title, linkedin_url=hit.link,
                        sources=["linkedin_search"],
                        title_relevance=title_relevance(title, ctx.titles),
                    )
                    person.evidence["linkedin_search"] = {
                        "query": res.query, "backend": res.backend,
                        "snippet": (hit.snippet or "")[:200],
                    }
                    merge_person(ctx, person)
            if queries:
                credits["websearch"] = float(queries)
            notes["queries"] = queries
        ctx.people.sort(key=lambda p: (-p.title_relevance, 0 if p.email else 1))
        notes["people"] = [p.full_name for p in ctx.people]
        return StepResult(self.name, credits=credits, notes=notes)
