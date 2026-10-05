import json
from pathlib import Path

from jobfinder import paths
from jobfinder.llm.fake import FakeLLM
from jobfinder.tailoring.master_schema import MasterResume, cover_letter_path, master_exists
from jobfinder.tailoring.translate import ensure_master, load_cover_letter

FIXTURE = Path(__file__).parent.parent / "fixtures" / "master" / "resume.yaml"


def test_ensure_master_fr_creates_files_once(home) -> None:
    (paths.master_dir() / "resume.yaml").write_text(FIXTURE.read_text())
    (paths.master_dir() / "cover-letter.md").write_text("Dear Hiring Manager,\n\nBody.\n")
    fr = MasterResume.from_yaml(FIXTURE).model_dump(mode="json")
    fr["summary"] = "Analyste en rapports."
    llm = FakeLLM({
        "translate_master": fr, "translate_text": {"text": "Madame, Monsieur,\n\nCorps."},
    })
    m = ensure_master("fr", llm)
    assert m.summary == "Analyste en rapports." and master_exists("fr")
    assert load_cover_letter("fr").startswith("Madame, Monsieur")
    ensure_master("fr", llm)
    assert len(llm.calls) == 2  # no re-translation
    assert ensure_master("en", llm).summary.startswith("Reporting analyst")


def test_load_cover_letter_unwraps_json_shell(home) -> None:
    cover_letter_path("fr").write_text(
        json.dumps({"text": "Madame,\n\nCorps."}), encoding="utf-8"
    )
    assert load_cover_letter("fr") == "Madame,\n\nCorps.\n"


def test_load_cover_letter_leaves_plain_text(home) -> None:
    cover_letter_path("en").write_text("{Braces} open.\n\nBody.\n", encoding="utf-8")
    assert load_cover_letter("en") == "{Braces} open.\n\nBody.\n"


def test_translated_cover_is_saved_as_text(home) -> None:
    (paths.master_dir() / "resume.yaml").write_text(FIXTURE.read_text())
    (paths.master_dir() / "cover-letter.md").write_text("Dear Hiring Manager,\n\nBody.\n")
    fr = MasterResume.from_yaml(FIXTURE).model_dump(mode="json")
    llm = FakeLLM({
        "translate_master": fr,
        "translate_text": {"text": json.dumps({"text": "Madame,\n\nCorps."})},
    })
    ensure_master("fr", llm)
    assert cover_letter_path("fr").read_text(encoding="utf-8") == "Madame,\n\nCorps.\n"
