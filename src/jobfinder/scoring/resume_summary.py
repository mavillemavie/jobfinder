from __future__ import annotations

from jobfinder.tailoring.master_schema import MasterResume


def build_resume_summary(master: MasterResume, *, max_roles: int = 4, max_bullets: int = 3) -> str:
    """Compact, contact-free view of the master resume for scoring prompts."""
    lines: list[str] = []
    if master.summary:
        lines += ["SUMMARY", master.summary.strip(), ""]
    if master.skills:
        lines.append("SKILLS")
        for g in master.skills:
            lines.append(f"- {g.category}: {', '.join(g.items)}")
        lines.append("")
    if master.experience:
        lines.append("EXPERIENCE")
        for e in master.experience[:max_roles]:
            lines.append(f"- {e.title} — {e.company} ({e.start or '?'} → {e.end or '?'})")
            for b in e.bullets[:max_bullets]:
                lines.append(f"    • {b}")
        lines.append("")
    if master.education:
        lines.append("EDUCATION")
        for ed in master.education:
            bits = [ed.credential, ed.field, ed.institution, ed.year]
            lines.append("- " + ", ".join(str(b) for b in bits if b))
        lines.append("")
    if master.certifications:
        lines.append("CERTIFICATIONS: " + "; ".join(master.certifications))
    if master.languages:
        lang_str = ", ".join(
            f"{lg.name} ({lg.level})" if lg.level else lg.name
            for lg in master.languages
        )
        lines.append("LANGUAGES: " + lang_str)
    return "\n".join(lines).strip()
