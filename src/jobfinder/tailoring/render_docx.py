from __future__ import annotations

from datetime import date
from pathlib import Path

from docx import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from jobfinder.tailoring.master_schema import MasterResume

HEADINGS: dict[str, dict[str, str]] = {
    "en": {
        "summary": "SUMMARY", "skills": "SKILLS", "experience": "EXPERIENCE",
        "education": "EDUCATION", "certifications": "CERTIFICATIONS", "languages": "LANGUAGES",
        "projects": "PROJECTS",
    },
    "fr": {
        "summary": "SOMMAIRE", "skills": "COMPÉTENCES", "experience": "EXPÉRIENCE",
        "education": "FORMATION", "certifications": "CERTIFICATIONS", "languages": "LANGUES",
        "projects": "PROJETS",
    },
}
BODY_FONT = "Calibri"
BODY_PT = 10.5
MARGIN_IN = 0.7
TEXT_WIDTH_IN = 8.5 - 2 * MARGIN_IN
MUTED = RGBColor(0x55, 0x55, 0x55)
_MONTHS = {
    "en": [
        "January", "February", "March", "April", "May", "June", "July", "August",
        "September", "October", "November", "December",
    ],
    "fr": [
        "janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre",
    ],
}


def letter_date(when: date, lang: str) -> str:
    if lang == "fr":
        return f"{when.day} {_MONTHS['fr'][when.month - 1]} {when.year}"
    return f"{_MONTHS['en'][when.month - 1]} {when.day}, {when.year}"


def _new_document() -> DocxDocument:
    doc = DocxDocument()
    normal = doc.styles["Normal"]
    normal.font.name = BODY_FONT
    normal.font.size = Pt(BODY_PT)
    for section in doc.sections:
        section.top_margin = section.bottom_margin = Inches(MARGIN_IN)
        section.left_margin = section.right_margin = Inches(MARGIN_IN)
    return doc


_PBDR_SUCCESSORS = (
    "w:shd", "w:tabs", "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap", "w:overflowPunct",
    "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN", "w:bidi", "w:adjustRightInd",
    "w:snapToGrid", "w:spacing", "w:ind", "w:contextualSpacing", "w:mirrorIndents",
    "w:suppressOverlap", "w:jc", "w:textDirection", "w:textAlignment", "w:textboxTightWrap",
    "w:outlineLvl", "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange",
)


def _bottom_rule(paragraph) -> None:
    bdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for k, v in (("w:val", "single"), ("w:sz", "4"), ("w:space", "1"), ("w:color", "888888")):
        bottom.set(qn(k), v)
    bdr.append(bottom)
    # schema order: pBdr comes before shd, tabs, spacing, ind, jc … (Word is strict about it)
    paragraph._p.get_or_add_pPr().insert_element_before(bdr, *_PBDR_SUCCESSORS)


def _heading(doc: DocxDocument, text: str) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(3)
    run = p.add_run(text)
    run.bold = True
    run.font.size = Pt(10.5)
    _bottom_rule(p)


def _contact_line(resume: MasterResume) -> str:
    c = resume.contact
    return " | ".join(x for x in [c.location, c.phone, c.email, c.linkedin, c.website] if x)


def _name_block(doc: DocxDocument, resume: MasterResume) -> None:
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = p.add_run(resume.contact.name)
    run.bold = True
    run.font.size = Pt(18)
    contact = doc.add_paragraph().add_run(_contact_line(resume))
    contact.font.size = Pt(9.5)
    contact.font.color.rgb = MUTED


def _bullet(doc: DocxDocument, text: str):
    p = doc.add_paragraph(text, style="List Bullet")
    p.paragraph_format.space_after = Pt(2)
    return p


def _dates(start: str | None, end: str | None) -> str:
    return f"{start or ''} – {end or ''}".strip(" –")


def render_resume_docx(resume: MasterResume, path: Path, lang: str = "en") -> Path:
    h = HEADINGS.get(lang, HEADINGS["en"])
    doc = _new_document()
    _name_block(doc, resume)
    if resume.summary:
        _heading(doc, h["summary"])
        doc.add_paragraph(resume.summary.strip())
    if resume.skills:
        _heading(doc, h["skills"])
        for g in resume.skills:
            p = doc.add_paragraph()
            r = p.add_run(f"{g.category}: ")
            r.bold = True
            p.add_run(", ".join(g.items))
    if resume.experience:
        _heading(doc, h["experience"])
        for e in resume.experience:
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(4)
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.tab_stops.add_tab_stop(
                Inches(TEXT_WIDTH_IN), WD_TAB_ALIGNMENT.RIGHT
            )
            p.add_run(f"{e.title} — {e.company}").bold = True
            dates = _dates(e.start, e.end)
            if dates:
                p.add_run(f"\t{dates}")
            if e.location:
                lp = doc.add_paragraph()
                lp.paragraph_format.space_after = Pt(1)
                loc = lp.add_run(e.location)
                loc.italic = True
                loc.font.size = Pt(9.5)
                loc.font.color.rgb = MUTED
            for b in e.bullets:
                _bullet(doc, b)
    if resume.education:
        _heading(doc, h["education"])
        for ed in resume.education:
            bits = [ed.credential, ed.field, ed.institution, ed.year]
            doc.add_paragraph(", ".join(str(b) for b in bits if b))
    if resume.certifications:
        _heading(doc, h["certifications"])
        for c in resume.certifications:
            _bullet(doc, c)
    if resume.languages:
        _heading(doc, h["languages"])
        doc.add_paragraph(
            ", ".join(f"{lg.name} ({lg.level})" if lg.level else lg.name for lg in resume.languages)
        )
    if resume.projects:
        _heading(doc, h["projects"])
        for pr in resume.projects:
            p = _bullet(doc, "")
            r = p.add_run(f"{pr.name}: ")
            r.bold = True
            p.add_run(pr.description or "")
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path


def render_cover_letter_docx(
    text: str, resume: MasterResume, path: Path, lang: str = "en", when: date | None = None
) -> Path:
    doc = _new_document()
    _name_block(doc, resume)
    dated = doc.add_paragraph(letter_date(when or date.today(), lang))
    dated.paragraph_format.space_before = Pt(12)
    dated.paragraph_format.space_after = Pt(10)
    for para in [p.strip() for p in text.split("\n\n") if p.strip()]:
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(8)
        p.add_run(para).font.size = Pt(11)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path


def docx_text(path: Path) -> str:
    doc = DocxDocument(str(path))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
