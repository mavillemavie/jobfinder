from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Protocol

from jsonschema import Draft202012Validator

Tier = Literal["fast", "strong"]

_PROMPTS = Path(__file__).parent / "prompts"
_SCHEMAS = Path(__file__).parent / "schemas"


class LLMError(Exception):
    """Provider failed (process error, auth, timeout)."""


class LLMOutputError(LLMError):
    """Provider answered but the JSON did not satisfy the schema after a retry."""


class LLMProvider(Protocol):
    name: str

    def complete_json(
        self,
        *,
        task: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        tier: Tier = "fast",
        language: str = "en",
    ) -> dict[str, Any]: ...


def load_prompt(name: str) -> str:
    return (_PROMPTS / f"{name}.md").read_text(encoding="utf-8")


def load_schema(name: str) -> dict[str, Any]:
    return json.loads((_SCHEMAS / f"{name}.json").read_text(encoding="utf-8"))


def validate_json(data: Any, schema: dict[str, Any]) -> list[str]:
    """Return human-readable validation errors (empty list = valid)."""
    validator = Draft202012Validator(schema)
    return [
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    ]
