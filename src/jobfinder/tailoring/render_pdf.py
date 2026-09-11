from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from pypdf import PdfReader
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

from jobfinder.tailoring.master_schema import MasterResume
from jobfinder.tailoring.render_docx import HEADINGS

_BASE = getSampleStyleSheet()
BODY = ParagraphStyle(
    "body", parent=_BASE["Normal"], fontName="Helvetica", fontSize=10.5, leading=13.5,
    alignment=TA_LEFT,
)
NAME = ParagraphStyle(
    "name", parent=BODY, fontName="Helvetica-Bold", fontSize=16, leading=19, spaceAfter=2
)
HEAD = ParagraphStyle(
    "head", parent=BODY, fontName="Helvetica-Bold", fontSize=11, leading=14, spaceBefore=8,
    spaceAfter=2,
)
BOLD = ParagraphStyle("bold", parent=BODY, fontName="Helvetica-Bold")


def _doc(path: Path) -> SimpleDocTemplate:
    path.parent.mkdir(parents=True, exist_ok=True)
    return SimpleDocTemplate(
        str(path), pagesize=letter, leftMargin=0.8 * inch, rightMargin=0.8 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
    )


def _p(text: str, style: ParagraphStyle = BODY) -> Paragraph:
    return Paragraph(escape(text), style)


def _bullets(items: list[str]) -> ListFlowable:
    return ListFlowable(
        [ListItem(_p(i), leftIndent=12) for i in items], bulletType="bullet", start="•",
        leftIndent=12,
    )


def _contact_line(resume: MasterResume) -> str:
    c = resume.contact
    return " | ".join(x for x in [c.location, c.phone, c.email, c.linkedin, c.website] if x)


def _dates(start: str | None, end: str | None) -> str:
    return f"{start or ''} – {end or ''}".strip(" –")


def render_resume_pdf(resume: MasterResume, path: Path, lang: str = "en") -> Path:
    h = HEADINGS.get(lang, HEADINGS["en"])
    story = [_p(resume.contact.name, NAME), _p(_contact_line(resume))]
    if resume.summary:
        story += [_p(h["summary"], HEAD), _p(resume.summary.strip())]
    if resume.skills:
        story.append(_p(h["skills"], HEAD))
        for g in resume.skills:
            story.append(
                Paragraph(f"<b>{escape(g.category)}:</b> {escape(', '.join(g.items))}", BODY)
            )
    if resume.experience:
        story.append(_p(h["experience"], HEAD))
        for e in resume.experience:
            meta = " | ".join(x for x in [e.location, _dates(e.start, e.end)] if x)
            head = f"<b>{escape(e.title)} — {escape(e.company)}</b>"
            story.append(Paragraph(head + (f"  ({escape(meta)})" if meta else ""), BODY))
            if e.bullets:
                story.append(_bullets(e.bullets))
    if resume.education:
        story.append(_p(h["education"], HEAD))
        for ed in resume.education:
            bits = [ed.credential, ed.field, ed.institution, ed.year]
            story.append(_p(", ".join(str(b) for b in bits if b)))
    if resume.certifications:
        story += [_p(h["certifications"], HEAD), _bullets(resume.certifications)]
    if resume.languages:
        langs = ", ".join(
            f"{lg.name} ({lg.level})" if lg.level else lg.name for lg in resume.languages
        )
        story += [_p(h["languages"], HEAD), _p(langs)]
    if resume.projects:
        story += [
            _p(h["projects"], HEAD),
            _bullets([f"{pr.name}: {pr.description or ''}" for pr in resume.projects]),
        ]
    _doc(path).build(story)
    return path


def render_cover_letter_pdf(text: str, resume: MasterResume, path: Path) -> Path:
    story = [_p(resume.contact.name, NAME), _p(_contact_line(resume)), Spacer(1, 12)]
    for para in [p.strip() for p in text.split("\n\n") if p.strip()]:
        story += [_p(para), Spacer(1, 8)]
    _doc(path).build(story)
    return path


def pdf_text(path: Path) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def pdf_pages(path: Path) -> int:
    return len(PdfReader(str(path)).pages)
