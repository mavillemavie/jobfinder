from __future__ import annotations

import json

from jobfinder.llm.base import LLMProvider, load_prompt, load_schema
from jobfinder.tailoring.master_schema import (
    MasterResume,
    cover_letter_path,
    load_master,
    master_exists,
    master_resume_path,
)

_COVER_SYSTEM = (
    "Translate this cover letter to Canadian French. Keep facts identical. Return JSON {text}."
)


def _unwrap(text: str) -> str:
    """A cover letter saved as a provider's raw JSON ({"text": ...}) is read as its text."""
    s = text.strip()
    if s.startswith("{"):
        try:
            data = json.loads(s)
        except ValueError:
            return text
        if isinstance(data, dict) and isinstance(data.get("text"), str):
            return data["text"].strip() + "\n"
    return text


def load_cover_letter(lang: str = "en") -> str:
    path = cover_letter_path(lang)
    return _unwrap(path.read_text(encoding="utf-8")) if path.exists() else ""


def ensure_master(lang: str, llm: LLMProvider) -> MasterResume:
    """English master as-is; for another language, translate once and cache under master/."""
    if lang == "en" or master_exists(lang):
        return load_master(lang)
    english = load_master("en")
    data = llm.complete_json(
        task="translate_master", system=load_prompt("translate_master"),
        user=english.model_dump_json(indent=2), schema=MasterResume.model_json_schema(),
        tier="strong", language=lang,
    )
    translated = MasterResume.model_validate(data)
    translated.to_yaml(master_resume_path(lang))
    en_cover = load_cover_letter("en")
    if en_cover:
        out = llm.complete_json(
            task="translate_text", system=_COVER_SYSTEM, user=en_cover,
            schema=load_schema("translate_text"), tier="strong", language=lang,
        )
        cover_letter_path(lang).write_text(
            _unwrap(out["text"]).strip() + "\n", encoding="utf-8"
        )
    return translated
