from __future__ import annotations

from collections.abc import Callable
from typing import Any

from jobfinder.llm.base import LLMError, Tier

Canned = dict[str, Any] | list[dict[str, Any]] | Callable[[str], dict[str, Any]]


class FakeLLM:
    """Deterministic provider for tests: responses keyed by task name."""

    name = "fake"

    def __init__(self, responses: dict[str, Canned] | None = None) -> None:
        self.responses: dict[str, Canned] = dict(responses or {})
        self.calls: list[dict[str, Any]] = []

    def complete_json(
        self,
        *,
        task: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        tier: Tier = "fast",
        language: str = "en",
    ) -> dict[str, Any]:
        self.calls.append(
            {"task": task, "system": system, "user": user, "tier": tier, "language": language}
        )
        if task not in self.responses:
            raise LLMError(f"FakeLLM has no response for task {task!r}")
        canned = self.responses[task]
        if callable(canned):
            return canned(user)
        if isinstance(canned, list):
            if not canned:
                raise LLMError(f"FakeLLM ran out of responses for task {task!r}")
            return canned.pop(0)
        return canned
