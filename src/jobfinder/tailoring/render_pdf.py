from __future__ import annotations

from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    Flowable,
    HRFlowable,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from jobfinder.tailoring.master_schema import MasterResume
from jobfinder.tailoring.render_docx import HEADINGS, MARGIN_IN, letter_date

MARGIN = MARGIN_IN * inch
FRAME_PAD = 6  # SimpleDocTemplate frame padding on each side
GREY = colors.HexColor("#555555")
RULE = colors.HexColor("#888888")

_BASE = getSampleStyleSheet()
BODY = ParagraphStyle(
    "body", parent=_BASE["Normal"], fontName="Helvetica", fontSize=10.5, leading=13.5,
    alignment=TA_LEFT,
)
NAME = ParagraphStyle(
    "name", parent=BODY, fontName="Helvetica-Bold", fontSize=18, leading=21, spaceAfter=2
)
CONTACT = ParagraphStyle("contact", parent=BODY, fontSize=9.5, leading=12, textColor=GREY)
HEAD = ParagraphStyle(
    "head", parent=BODY, fontName="Helvetica-Bold", fontSize=10.5, leading=13, spaceBefore=8,
    spaceAfter=1,
)
MUTED_I = ParagraphStyle(
    "muted_i", parent=BODY, fontName="Helvetica-Oblique", fontSize=9.5, leading=12,
    textColor=GREY, spaceAfter=1,
)
RIGHT = ParagraphStyle("right", parent=BODY, alignment=TA_RIGHT)
LETTER = ParagraphStyle("letter", parent=BODY, fontSize=11, leading=14.5)


def _doc(path: Path) -> SimpleDocTemplate:
    path.parent.mkdir(parents=True, exist_ok=True)
    return SimpleDocTemplate(
        str(path), pagesize=letter, leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
    )


def _p(text: str, style: ParagraphStyle = BODY) -> Paragraph:
    return Paragraph(escape(text), style)


def _bullets(items: list[str]) -> ListFlowable:
    return ListFlowable(
        [ListItem(_p(i), leftIndent=12, spaceAfter=2) for i in items], bulletType="bullet",
        start="•", leftIndent=12,
    )


def _heading(text: str) -> list[Flowable]:
    return [
        _p(text, HEAD),
        HRFlowable(width="100%", thickness=0.5, color=RULE, spaceBefore=0, spaceAfter=3),
    ]


def _role_row(title_markup: str, dates: str) -> Table:
    width = letter[0] - 2 * MARGIN - 2 * FRAME_PAD
    # the date column fits its text (long French ranges), at least a quarter of the line
    date_w = max(width * 0.25, stringWidth(dates, RIGHT.fontName, RIGHT.fontSize) + 4)
    t = Table(
        [[Paragraph(title_markup, BODY), _p(dates, RIGHT)]],
        colWidths=[width - date_w, date_w],
    )
    t.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
    ]))
    return t


def _contact_line(resume: MasterResume) -> str:
    c = resume.contact
    return " | ".join(x for x in [c.location, c.phone, c.email, c.linkedin, c.website] if x)


def _dates(start: str | None, end: str | None) -> str:
    return f"{start or ''} – {end or ''}".strip(" –")


def render_resume_pdf(resume: MasterResume, path: Path, lang: str = "en") -> Path:
    h = HEADINGS.get(lang, HEADINGS["en"])
    story: list[Flowable] = [_p(resume.contact.name, NAME), _p(_contact_line(resume), CONTACT)]
    if resume.summary:
        story += [*_heading(h["summary"]), _p(resume.summary.strip())]
    if resume.skills:
        story += _heading(h["skills"])
        for g in resume.skills:
            story.append(
                Paragraph(f"<b>{escape(g.category)}:</b> {escape(', '.join(g.items))}", BODY)
            )
    if resume.experience:
        story += _heading(h["experience"])
        for e in resume.experience:
            title = f"<b>{escape(e.title)} — {escape(e.company)}</b>"
            story.append(_role_row(title, _dates(e.start, e.end)))
            if e.location:
                story.append(_p(e.location, MUTED_I))
            if e.bullets:
                story.append(_bullets(e.bullets))
    if resume.education:
        story += _heading(h["education"])
        for ed in resume.education:
            bits = [ed.credential, ed.field, ed.institution, ed.year]
            story.append(_p(", ".join(str(b) for b in bits if b)))
    if resume.certifications:
        story += [*_heading(h["certifications"]), _bullets(resume.certifications)]
    if resume.languages:
        langs = ", ".join(
            f"{lg.name} ({lg.level})" if lg.level else lg.name for lg in resume.languages
        )
        story += [*_heading(h["languages"]), _p(langs)]
    if resume.projects:
        story += [
            *_heading(h["projects"]),
            _bullets([f"{pr.name}: {pr.description or ''}" for pr in resume.projects]),
        ]
    _doc(path).build(story)
    return path


def render_cover_letter_pdf(
    text: str, resume: MasterResume, path: Path, lang: str = "en", when: date | None = None
) -> Path:
    story: list[Flowable] = [
        _p(resume.contact.name, NAME), _p(_contact_line(resume), CONTACT), Spacer(1, 12),
        _p(letter_date(when or date.today(), lang), LETTER), Spacer(1, 10),
    ]
    for para in [p.strip() for p in text.split("\n\n") if p.strip()]:
        lines = "<br/>".join(escape(line) for line in para.splitlines())
        story += [Paragraph(lines, LETTER), Spacer(1, 8)]
    _doc(path).build(story)
    return path


def pdf_text(path: Path) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def pdf_pages(path: Path) -> int:
    return len(PdfReader(str(path)).pages)
