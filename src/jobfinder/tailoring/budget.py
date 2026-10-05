from __future__ import annotations

import re
from pathlib import Path

from jobfinder.tailoring.master_schema import MasterResume
from jobfinder.tailoring.render_pdf import pdf_pages, render_resume_pdf

MAX_ROLES = 4
BULLET_LADDER = (5, 4, 3, 2)
MAX_SKILL_GROUPS = 3
MAX_SKILL_ITEMS = 15
MAX_PROJECTS = 2
MAX_CERTS = 4
MAX_EDUCATION = 3
MAX_SUMMARY_WORDS = 60
MAX_PAGES = 2
MAX_FIT_STEPS = 30

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _trim_summary(text: str) -> str:
    """Whole sentences up to MAX_SUMMARY_WORDS; the first sentence is always kept whole.
    Text without sentence punctuation is cut at a word boundary."""
    sentences = [s for s in _SENTENCE_END.split(text.strip()) if s]
    if len(sentences) == 1 and not sentences[0].rstrip().endswith((".", "!", "?")):
        return " ".join(text.split()[:MAX_SUMMARY_WORDS])
    kept: list[str] = []
    for s in sentences:
        if kept and len(" ".join([*kept, s]).split()) > MAX_SUMMARY_WORDS:
            break
        kept.append(s)
    return " ".join(kept)


def apply_budget(resume: MasterResume) -> tuple[MasterResume, list[str]]:
    """Section caps in code (the prompt's 'two pages' was ignored 15/15). The LLM's order is
    taken as relevance order: later roles, bullets, items and entries are cut first."""
    r = resume.model_copy(deep=True)
    cuts: list[str] = []
    if len(r.experience) > MAX_ROLES:
        cuts.append(f"roles: kept {MAX_ROLES} of {len(r.experience)}")
        r.experience = r.experience[:MAX_ROLES]
    for i, e in enumerate(r.experience):
        cap = BULLET_LADDER[min(i, len(BULLET_LADDER) - 1)]
        if len(e.bullets) > cap:
            cuts.append(f"bullets: {e.company} kept {cap} of {len(e.bullets)}")
            e.bullets = e.bullets[:cap]
    if len(r.skills) > MAX_SKILL_GROUPS:
        cuts.append(f"skills: kept {MAX_SKILL_GROUPS} groups of {len(r.skills)}")
        r.skills = r.skills[:MAX_SKILL_GROUPS]
    total = sum(len(g.items) for g in r.skills)
    if total > MAX_SKILL_ITEMS:
        cuts.append(f"skills: kept {MAX_SKILL_ITEMS} items of {total}")
        for _ in range(total - MAX_SKILL_ITEMS):
            # the largest group loses its last item; ties go to the later group
            max(reversed(r.skills), key=lambda g: len(g.items)).items.pop()
        r.skills = [g for g in r.skills if g.items]
    for field, cap in (
        ("projects", MAX_PROJECTS), ("certifications", MAX_CERTS), ("education", MAX_EDUCATION)
    ):
        items = getattr(r, field)
        if len(items) > cap:
            cuts.append(f"{field}: kept {cap} of {len(items)}")
            setattr(r, field, items[:cap])
    before = len(r.summary.split())
    if before > MAX_SUMMARY_WORDS:
        r.summary = _trim_summary(r.summary)
        after = len(r.summary.split())
        if after < before:
            cuts.append(f"summary: trimmed to {after} words")
        else:
            cuts.append(f"summary: over budget (one sentence, {after} words)")
    return r, cuts


def _cut_one(r: MasterResume) -> str | None:
    """One more unit off the bottom, least valuable first."""
    for e in reversed(r.experience):
        if len(e.bullets) > 1:
            e.bullets.pop()
            return f"page fit: dropped a bullet from {e.company}"
    if r.projects:
        r.projects.pop()
        return "page fit: dropped a project"
    for g in reversed(r.skills):
        if len(g.items) > 1:
            g.items.pop()
            return f"page fit: dropped a skill from {g.category}"
    if len(r.experience) > 1:
        return f"page fit: dropped role {r.experience.pop().company}"
    return None


def fit_two_pages(
    resume: MasterResume, pdf_path: Path, lang: str = "en"
) -> tuple[MasterResume, list[str], int]:
    """Render, and while the PDF runs past MAX_PAGES cut one unit and re-render (bounded).
    The PDF left at `pdf_path` is the render of the returned résumé."""
    r = resume.model_copy(deep=True)
    cuts: list[str] = []
    render_resume_pdf(r, pdf_path, lang)
    pages = pdf_pages(pdf_path)
    while pages > MAX_PAGES and len(cuts) < MAX_FIT_STEPS:
        cut = _cut_one(r)
        if cut is None:
            break
        cuts.append(cut)
        render_resume_pdf(r, pdf_path, lang)
        pages = pdf_pages(pdf_path)
    return r, cuts, pages
