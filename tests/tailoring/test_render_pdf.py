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
