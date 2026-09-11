"""Master seeding: find the vocabulary the pipeline keeps reporting as missing, and weave
JF-confirmed terms into the master résumé without inventing claims."""
from __future__ import annotations

import difflib
import re
from collections import defaultdict
from datetime import datetime
from io import StringIO

from ruamel.yaml import YAML
from sqlalchemy import select
from sqlalchemy.orm import Session

from jobfinder.db.models import Document, Posting, Score
from jobfinder.llm.base import LLMProvider, load_prompt
from jobfinder.tailoring.master_schema import MasterResume
from jobfinder.tailoring.requirements import extract_requirement_terms
from jobfinder.tailoring.truth_check import deterministic_truth_check

_LEAD = re.compile(r"^\s*([A-Za-z][A-Za-z0-9+#./ -]{1,40}?)\s*(?:—|–|:|\()")
_PREAMBLE = re.compile(
    r"^(?:experience|expertise|proficiency|knowledge|familiarity|background|skills?)\s+"
    r"(?:with|in|of|using)\s+",
    re.I,
)


def _terms_from_text(text: str, *, lead: bool = False) -> set[str]:
    """Vocabulary terms present in `text`; with `lead`, also the leading noun phrase of a
    'Term — explanation' sentence (how the tailoring prompt phrases its gaps)."""
    found = set(extract_requirement_terms(text or ""))
    if lead:
        m = _LEAD.match(text or "")
        if m:
            phrase = _PREAMBLE.sub("", " ".join(m.group(1).lower().split()))
            if 1 <= len(phrase.split()) <= 4 and "as such" not in phrase:
                found.add(phrase)
    return found


def _master_has(term: str, master_text: str, overlap: set[str]) -> bool:
    if term in overlap:
        return True
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", master_text) is not None


def gap_report(
    session: Session, master: MasterResume, *, since: datetime, top: int = 25
) -> list[dict]:
    """Terms postings wanted and the master does not say, ranked by how often they came up."""
    overlap = master.overlap_terms()
    master_text = master.model_dump_json().lower()
    counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"scores": 0, "documents": 0, "drops": 0}
    )

    def add(terms: set[str], bucket: str) -> None:
        for t in terms:
            if not _master_has(t, master_text, overlap):
                counts[t][bucket] += 1

    for sc in session.scalars(select(Score).where(Score.created_at >= since)):
        terms: set[str] = set()
        for item in sc.missing_requirements or []:
            terms |= _terms_from_text(str(item), lead=True)
        add(terms, "scores")
    docs = session.scalars(
        select(Document).where(Document.created_at >= since, Document.kind == "resume",
                               Document.format == "docx")
    )
    for d in docs:
        rep = d.ats_report or {}
        terms = {str(t).lower() for t in rep.get("keyword_missing", [])}
        for g in rep.get("gaps", []):
            terms |= _terms_from_text(str(g), lead=True)
        add(terms, "documents")
    drops = session.scalars(
        select(Posting).where(Posting.status == "prefiltered_out", Posting.last_seen_at >= since)
    )
    for p in drops:
        if (p.prefilter_result or {}).get("reason") == "low_overlap":
            add(_terms_from_text(p.description_text or ""), "drops")
    rows = [
        {"term": t, "count": sum(b.values()), **b} for t, b in counts.items() if sum(b.values())
    ]
    rows.sort(key=lambda r: (-r["count"], r["term"]))
    return rows[:top]


# --- seeding: weave confirmed terms into the master under a truth guard -----------------------


class SeedRejected(Exception):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def _present(term: str, text: str) -> bool:
    """Word-boundary match that tolerates plain inflections of the last word
    ("stakeholder" → "stakeholders", "forecast" → "forecasting")."""
    pattern = rf"(?<![a-z0-9]){re.escape(term.lower())}[a-z]{{0,3}}(?![a-z0-9])"
    return re.search(pattern, text) is not None


def seed_guard(old: MasterResume, new: MasterResume, terms: list[str]) -> list[str]:
    """Reasons to refuse `new`: any new claim beyond the confirmed terms, a term that was not
    placed, anything removed, or a changed contact block."""
    wanted = [t.lower() for t in terms]
    reasons: list[str] = []
    for finding in deterministic_truth_check(new, old):
        if finding.startswith("skill not in master: "):
            item = finding.split(": ", 1)[1].lower()
            if any(w in item for w in wanted):
                continue
        reasons.append(finding)
    new_text = new.model_dump_json().lower()
    reasons += [f"term not placed: {w}" for w in wanted if not _present(w, new_text)]
    if len(new.experience) < len(old.experience):
        reasons.append("removed: experience entry")
    for o, n in zip(old.experience, new.experience, strict=False):
        if len(n.bullets) < len(o.bullets):
            reasons.append(f"removed: bullet in {o.company}")
    if len(new.skills) < len(old.skills):
        reasons.append("removed: skill group")
    for o, n in zip(old.skills, new.skills, strict=False):
        if len(n.items) < len(o.items):
            reasons.append(f"removed: skill item in {o.category}")
    for field in ("education", "certifications", "languages", "projects"):
        if len(getattr(new, field)) < len(getattr(old, field)):
            reasons.append(f"removed: {field}")
    if new.contact != old.contact:
        reasons.append("contact block changed")
    return reasons


def change_log(old: MasterResume, new: MasterResume) -> list[str]:
    changes: list[str] = []
    for o, n in zip(old.experience, new.experience, strict=False):
        for i, (ob, nb) in enumerate(zip(o.bullets, n.bullets, strict=False)):
            if ob != nb:
                changes.append(f"{o.company}: bullet {i + 1} extended")
        changes += [f"{o.company}: bullet added" for _ in n.bullets[len(o.bullets):]]
    for o, n in zip(old.skills, new.skills, strict=False):
        added = [x for x in n.items if x not in o.items]
        if added:
            changes.append(f"skills/{o.category}: added {', '.join(added)}")
    changes += [f"skills: new group {n.category}" for n in new.skills[len(old.skills):]]
    if old.summary != new.summary:
        changes.append("summary reworded")
    return changes


def _yaml(master: MasterResume) -> list[str]:
    buf = StringIO()
    YAML().dump(master.model_dump(mode="json"), buf)
    return buf.getvalue().splitlines(keepends=True)


def master_diff(old: MasterResume, new: MasterResume) -> str:
    return "".join(difflib.unified_diff(
        _yaml(old), _yaml(new), "master/resume.yaml (current)", "master/resume.yaml (proposed)"
    ))


def propose_seed(
    master: MasterResume, terms: list[str], llm: LLMProvider, notes: str = ""
) -> tuple[MasterResume, list[str]]:
    """One strong-tier call that weaves `terms` into the master; the guard decides. `notes`
    carries the candidate's nuance ("Python: working knowledge, not a primary language")."""
    user = (
        f"CONFIRMED TERMS (true for the candidate): {', '.join(terms)}\n"
        + (f"PLACEMENT NOTES FROM THE CANDIDATE (respect the stated level): {notes}\n"
           if notes else "")
        + f"\nMASTER RESUME (JSON):\n{master.model_dump_json(indent=2)}"
    )
    data = llm.complete_json(
        task="seed_master", system=load_prompt("seed_master"), user=user,
        schema=MasterResume.model_json_schema(), tier="strong",
    )
    new = MasterResume.model_validate(data)
    new.contact = master.contact
    reasons = seed_guard(master, new, terms)
    if reasons:
        raise SeedRejected(reasons)
    return new, change_log(master, new)
