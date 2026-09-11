from __future__ import annotations

import re
from io import StringIO

from ruamel.yaml import YAML

from jobfinder.llm.base import LLMProvider, load_prompt, load_schema
from jobfinder.tailoring.master_schema import MasterResume

# A number may end a sentence ("… by 30%.") — only a following word char or a decimal
# continuation disqualifies the match, not a plain period.
_NUM = re.compile(r"(?<![\w.])(\$?\d[\d,]*(?:\.\d+)?(?:\s?%|k|K|M)?)(?!\w)(?!\.\d)")


def _norm(s: str | None) -> str:
    return " ".join((s or "").lower().split())


def _master_text(master: MasterResume) -> str:
    return _norm(master.model_dump_json())


def _numbers(text: str) -> set[str]:
    return {m.group(1).replace(",", "").replace(" ", "").lower() for m in _NUM.finditer(text)}


def deterministic_truth_check(tailored: MasterResume, master: MasterResume) -> list[str]:
    findings: list[str] = []
    mtext = _master_text(master)
    m_companies = {_norm(e.company) for e in master.experience}
    m_titles = {(_norm(e.company), _norm(e.title)) for e in master.experience}
    for e in tailored.experience:
        if _norm(e.company) not in m_companies:
            findings.append(f"employer not in master: {e.company}")
        elif (_norm(e.company), _norm(e.title)) not in m_titles:
            findings.append(f"title not in master for {e.company}: {e.title}")
        for field in ("start", "end"):
            val = getattr(e, field)
            if val and _norm(val) not in mtext:
                findings.append(f"date not in master ({e.company}.{field}): {val}")
    m_institutions = {_norm(ed.institution) for ed in master.education}
    for ed in tailored.education:
        if _norm(ed.institution) not in m_institutions:
            findings.append(f"institution not in master: {ed.institution}")
        for field in ("credential", "field", "year"):
            val = getattr(ed, field)
            if val and _norm(val) not in mtext:
                findings.append(f"education detail not in master ({field}): {val}")
    for cert in tailored.certifications:
        if _norm(cert) not in mtext:
            findings.append(f"certification not in master: {cert}")
    m_terms = master.skill_terms()
    for g in tailored.skills:
        for item in g.items:
            if item.lower().strip() not in m_terms and _norm(item) not in mtext:
                findings.append(f"skill not in master: {item}")
    for lg in tailored.languages:
        if _norm(lg.name) not in mtext:
            findings.append(f"language not in master: {lg.name}")
    prose = " ".join(
        [tailored.summary]
        + [b for e in tailored.experience for b in e.bullets]
        + [p.description or "" for p in tailored.projects]
    )
    master_nums = _numbers(master.model_dump_json())
    for num in sorted(_numbers(prose) - master_nums):
        findings.append(f"number not in master: {num}")
    return findings


def _dump(m: MasterResume) -> str:
    buf = StringIO()
    YAML().dump(m.model_dump(mode="json"), buf)
    return buf.getvalue()


def llm_truth_check(
    tailored: MasterResume, cover_letter: str, master: MasterResume, llm: LLMProvider
) -> list[str]:
    user = (
        f"MASTER RESUME (truth):\n{_dump(master)}\n\nTAILORED RESUME:\n{_dump(tailored)}\n\n"
        f"COVER LETTER:\n{cover_letter}"
    )
    data = llm.complete_json(
        task="truth_check", system=load_prompt("truth_check"), user=user,
        schema=load_schema("truth_check"), tier="strong",
    )
    return list(data.get("unsupported_claims", []))


def run_truth_check(
    tailored: MasterResume, cover_letter: str, master: MasterResume, llm: LLMProvider
) -> dict:
    det = deterministic_truth_check(tailored, master)
    via_llm = llm_truth_check(tailored, cover_letter, master, llm)
    return {"deterministic": det, "llm": via_llm, "ok": not det and not via_llm}
