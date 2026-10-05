from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy.orm import Session

from jobfinder import paths
from jobfinder.config import Profile
from jobfinder.db.models import Document, Posting
from jobfinder.db.session import release_snapshot
from jobfinder.discovery.normalize import slugify
from jobfinder.llm.base import LLMProvider
from jobfinder.tailoring.ats_check import ATS_READY_THRESHOLD, ats_report
from jobfinder.tailoring.budget import MAX_PAGES, apply_budget, fit_two_pages
from jobfinder.tailoring.master_schema import MasterResume
from jobfinder.tailoring.models import TailoredOutput, yaml_dump
from jobfinder.tailoring.render_docx import render_cover_letter_docx, render_resume_docx
from jobfinder.tailoring.render_pdf import render_cover_letter_pdf
from jobfinder.tailoring.requirements import extract_requirement_terms
from jobfinder.tailoring.review import review_documents, with_rules
from jobfinder.tailoring.translate import ensure_master, load_cover_letter
from jobfinder.tailoring.truth_check import run_truth_check

MAX_POSTING_CHARS = 8000
MAX_COVER_WORDS = 350


def document_lang(posting: Posting, profile: Profile) -> str:
    mode = profile.documents.languages
    if mode in ("en", "fr"):
        return mode
    return "fr" if posting.language == "fr" else "en"


def output_dir_for(posting: Posting) -> Path:
    return paths.output_dir() / f"{slugify(posting.company.name)}-{slugify(posting.title)}"


def file_name(kind: str, company: str, pattern: str) -> str:
    return pattern.format(kind=kind, company=re.sub(r"[^A-Za-z0-9]+", "", company))


def _build_prompt(
    master: MasterResume, cover: str, posting: Posting, lang: str, terms: list[str]
) -> str:
    score = posting.latest_score
    score_block = ""
    if score is not None:
        missing = ", ".join(score.missing_requirements) or "none"
        score_block = (
            f"\nFIT SCORE: {score.fit_score}\nREASONS: {'; '.join(score.reasons)}\n"
            f"MISSING REQUIREMENTS PER SCORER: {missing}\n"
        )
    return (
        f"LANGUAGE: {lang}\n\nMASTER RESUME (truth):\n{yaml_dump(master)}\n\n"
        f"MASTER COVER LETTER (voice reference):\n{cover}\n"
        f"\nPOSTING\nTitle: {posting.title}\nCompany: {posting.company.name}\n"
        f"Location: {posting.location_raw or ''}\n"
        f"Requirement terms detected: {', '.join(terms)}\n{score_block}\n"
        f"{posting.description_text[:MAX_POSTING_CHARS]}"
    )


def tailor_posting(
    session: Session,
    posting_id: int,
    *,
    llm: LLMProvider,
    profile: Profile,
    lang: str | None = None,
) -> list[Document]:
    posting = session.get(Posting, posting_id)
    if posting is None:
        raise ValueError(f"no posting {posting_id}")
    lang = lang or document_lang(posting, profile)
    master = ensure_master(lang, llm)
    cover_ref = load_cover_letter(lang) or load_cover_letter("en")
    terms = extract_requirement_terms(posting.description_text, master.skill_terms())
    prompt = _build_prompt(master, cover_ref, posting, lang, terms)
    # Three LLM calls take minutes; the writes below must not start on the read snapshot
    # opened while building the prompt (see release_snapshot, outage 2026-09-09).
    release_snapshot(session)
    data = llm.complete_json(
        task="tailor_resume", system=with_rules("tailor_resume"), user=prompt,
        schema=TailoredOutput.model_json_schema(), tier="strong", language=lang,
    )
    tailored, review = review_documents(
        TailoredOutput.model_validate(data), master=master, master_cover=cover_ref,
        posting_block="POSTING\n" + prompt.split("\nPOSTING\n", 1)[-1], lang=lang, llm=llm,
    )
    resume, cuts = apply_budget(tailored.resume)
    resume.contact = master.contact  # contact block is never rewritten

    out_dir = output_dir_for(posting)
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = profile.documents.file_name_pattern
    company = posting.company.name
    resume_name = file_name("Resume", company, pattern)
    cover_name = file_name("Cover-Letter", company, pattern)
    r_pdf = out_dir / f"{resume_name}.pdf"
    resume, fit_cuts, _ = fit_two_pages(resume, r_pdf, lang)
    cuts += fit_cuts
    r_docx = render_resume_docx(resume, out_dir / f"{resume_name}.docx", lang)
    letter = tailored.cover_letter
    c_docx = render_cover_letter_docx(letter, resume, out_dir / f"{cover_name}.docx", lang)
    c_pdf = render_cover_letter_pdf(letter, resume, out_dir / f"{cover_name}.pdf", lang)
    truth = run_truth_check(resume, letter, master, llm, master_cover=cover_ref)
    report = ats_report(r_docx, r_pdf, terms, lang)
    words = len(letter.split())
    checks = (
        ("truth", not truth["ok"]),
        ("ats", report["score"] < ATS_READY_THRESHOLD),
        ("pages", report["pages"] > MAX_PAGES),
        ("cover_letter_length", words > MAX_COVER_WORDS),
    )
    holds = [reason for reason, failed in checks if failed]
    status = "needs_review" if holds else "ready"
    change_log = tailored.change_log + cuts
    report.update(
        gaps=tailored.gaps, llm_keyword_coverage=tailored.keyword_coverage.model_dump(),
        review=review, budget_cuts=cuts, hold_reasons=holds,
    )
    letter_report = {"hold_reasons": holds, "cover_letter_words": words}

    posting.documents.clear()  # delete-orphan cascade drops the previous generation on flush
    docs = [
        Document(
            posting_id=posting.id, kind="resume", language=lang, format="docx", path=str(r_docx),
            ats_score=report["score"], ats_report=report, truth_check=truth,
            change_log=change_log, status=status,
        ),
        Document(
            posting_id=posting.id, kind="resume", language=lang, format="pdf", path=str(r_pdf),
            ats_score=report["score"], ats_report=report, truth_check=truth,
            change_log=change_log, status=status,
        ),
        Document(
            posting_id=posting.id, kind="cover_letter", language=lang, format="docx",
            path=str(c_docx), ats_report=letter_report, truth_check=truth, status=status,
        ),
        Document(
            posting_id=posting.id, kind="cover_letter", language=lang, format="pdf",
            path=str(c_pdf), ats_report=letter_report, truth_check=truth, status=status,
        ),
    ]
    posting.documents.extend(docs)
    if (
        posting.pipeline is not None
        and posting.pipeline.stage in ("new", "shortlisted")
        and status == "ready"
    ):
        posting.pipeline.stage = "docs_ready"
    session.commit()
    return docs
