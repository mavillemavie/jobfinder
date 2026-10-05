from jobfinder.tailoring import budget
from jobfinder.tailoring.budget import MAX_FIT_STEPS, apply_budget, fit_two_pages
from jobfinder.tailoring.master_schema import (
    ContactInfo,
    Education,
    Experience,
    MasterResume,
    Project,
    SkillGroup,
)
from jobfinder.tailoring.render_pdf import pdf_pages


def big_resume(roles: int = 7, bullets: int = 9) -> MasterResume:
    return MasterResume(
        contact=ContactInfo(name="Jordan Test"),
        summary=" ".join(f"Sentence {i} has exactly six words." for i in range(15)),
        skills=[
            SkillGroup(category=f"G{g}", items=[f"s{g}-{i}" for i in range(10)])
            for g in range(4)
        ],
        experience=[
            Experience(
                company=f"Co{r}", title="Analyst", start="2020", end="2021",
                bullets=[
                    f"Role {r} bullet {b} with enough words to wrap a line or two "
                    "when rendered in a document." for b in range(bullets)
                ],
            )
            for r in range(roles)
        ],
        education=[Education(institution=f"U{i}") for i in range(5)],
        certifications=[f"Cert {i}" for i in range(6)],
        projects=[Project(name=f"P{i}", description="d") for i in range(4)],
    )


def test_caps_every_section_and_keeps_order() -> None:
    out, cuts = apply_budget(big_resume())
    assert [e.company for e in out.experience] == ["Co0", "Co1", "Co2", "Co3"]
    assert [len(e.bullets) for e in out.experience] == [5, 4, 3, 2]
    assert out.experience[0].bullets[0].startswith("Role 0 bullet 0")
    assert len(out.skills) == 3 and sum(len(g.items) for g in out.skills) == 15
    assert [g.items[0] for g in out.skills] == ["s0-0", "s1-0", "s2-0"]
    assert len(out.projects) == 2 and len(out.certifications) == 4 and len(out.education) == 3
    assert len(out.summary.split()) <= 60 and out.summary.endswith(".")
    assert any("roles" in c for c in cuts) and any("skills" in c for c in cuts)


def test_small_resume_untouched() -> None:
    small = big_resume(roles=2, bullets=2)
    small.skills = small.skills[:1]
    small.summary = "Short summary."
    small.projects, small.certifications, small.education = [], [], []
    out, cuts = apply_budget(small)
    assert out == small and cuts == []


def test_long_first_sentence_is_kept_whole() -> None:
    r = big_resume(roles=1, bullets=1)
    r.summary = " ".join(["word"] * 70) + ". Second sentence."
    out, _ = apply_budget(r)
    assert out.summary == " ".join(["word"] * 70) + "."


def test_input_not_mutated() -> None:
    r = big_resume()
    apply_budget(r)
    assert len(r.experience) == 7


def test_fit_two_pages_trims_until_it_fits(tmp_path) -> None:
    r = big_resume(roles=4, bullets=14)  # 3 pages as rendered
    out, cuts, pages = fit_two_pages(r, tmp_path / "r.pdf")
    assert pages == 2 and pdf_pages(tmp_path / "r.pdf") == 2
    assert cuts and all(c.startswith("page fit: dropped a bullet from Co3") for c in cuts[:1])
    assert len(out.experience[0].bullets) == 14  # the most relevant role is cut last


def test_fit_two_pages_no_cut_when_it_fits(tmp_path) -> None:
    r = big_resume(roles=1, bullets=2)
    out, cuts, pages = fit_two_pages(r, tmp_path / "r.pdf")
    assert out == r and cuts == [] and pages == 1


def test_fit_two_pages_is_bounded(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(budget, "pdf_pages", lambda p: 3)
    _, cuts, pages = fit_two_pages(big_resume(), tmp_path / "r.pdf")
    assert pages == 3 and len(cuts) <= MAX_FIT_STEPS


def test_unpunctuated_summary_is_cut_at_a_word_boundary() -> None:
    r = big_resume(roles=1, bullets=1)
    r.summary = " ".join(f"w{i}" for i in range(80))
    out, cuts = apply_budget(r)
    assert len(out.summary.split()) == 60 and out.summary.startswith("w0 w1")
    assert "summary: trimmed to 60 words" in cuts


def test_single_long_sentence_logged_honestly() -> None:
    r = big_resume(roles=1, bullets=1)
    r.summary = " ".join(["word"] * 70) + "."
    out, cuts = apply_budget(r)
    assert out.summary == r.summary
    assert "summary: over budget (one sentence, 70 words)" in cuts
