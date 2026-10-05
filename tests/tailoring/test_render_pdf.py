from datetime import date
from pathlib import Path

from jobfinder.tailoring.master_schema import MasterResume
from jobfinder.tailoring.render_pdf import pdf_text, render_cover_letter_pdf, render_resume_pdf

FIXTURE = Path(__file__).parent.parent / "fixtures" / "master" / "resume.yaml"


def test_resume_pdf_contains_sections(tmp_path: Path) -> None:
    m = MasterResume.from_yaml(FIXTURE)
    out = render_resume_pdf(m, tmp_path / "r.pdf")
    text = pdf_text(out)
    assert "Jordan Test" in text and "EXPERIENCE" in text and "Power BI dashboards" in text


def test_cover_letter_pdf(tmp_path: Path) -> None:
    m = MasterResume.from_yaml(FIXTURE)
    out = render_cover_letter_pdf("Dear Hiring Manager,\n\nBody.", m, tmp_path / "c.pdf")
    assert "Dear Hiring Manager" in pdf_text(out)


def test_resume_pdf_role_line_has_dates(tmp_path: Path) -> None:
    m = MasterResume.from_yaml(FIXTURE)
    text = pdf_text(render_resume_pdf(m, tmp_path / "r.pdf"))
    assert "2019-03 – Present" in text and "Montreal, QC" in text
    assert "(Montreal" not in text  # location has its own line, not the old "(… | …)" suffix


def test_cover_letter_pdf_date(tmp_path: Path) -> None:
    m = MasterResume.from_yaml(FIXTURE)
    out = render_cover_letter_pdf(
        "Madame,\n\nCorps.", m, tmp_path / "c.pdf", lang="fr", when=date(2026, 10, 4)
    )
    assert "4 octobre 2026" in pdf_text(out)


def test_cover_letter_pdf_keeps_sign_off_line_break(tmp_path: Path) -> None:
    m = MasterResume.from_yaml(FIXTURE)
    out = render_cover_letter_pdf("Body.\n\nWarm regards,\nJordan Test", m, tmp_path / "c.pdf")
    assert "Warm regards,\nJordan Test" in pdf_text(out)


def test_long_french_date_range_stays_on_one_line(tmp_path: Path) -> None:
    m = MasterResume.from_yaml(FIXTURE)
    m.experience[0].start, m.experience[0].end = "septembre 2020", "aujourd'hui (actuel)"
    text = pdf_text(render_resume_pdf(m, tmp_path / "r.pdf", lang="fr"))
    assert "septembre 2020 – aujourd'hui (actuel)" in text
