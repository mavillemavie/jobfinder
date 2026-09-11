from __future__ import annotations

import json
import time
from typing import Any

import httpx

from jobfinder import paths
from jobfinder.llm.base import LLMError, LLMOutputError, Tier, validate_json


class LMStudioProvider:
    """Local OpenAI-compatible server (LM Studio) with JSON-schema response_format."""

    name = "lmstudio"

    def __init__(self, base_url: str, model: str, client: httpx.Client | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.client = client or httpx.Client(timeout=600.0)

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
        prompt = user
        errors: list[str] = []
        for attempt in (1, 2):
            body = {
                "model": self.model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": task, "schema": schema},
                },
            }
            started = time.monotonic()
            try:
                resp = self.client.post(
                    f"{self.base_url}/v1/chat/completions", json=body
                )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                self._log(
                    task,
                    tier,
                    {},
                    time.monotonic() - started,
                    attempt,
                    error=str(exc)[:200],
                )
                raise LLMError(f"LM Studio request failed: {exc}") from exc
            elapsed = time.monotonic() - started
            try:
                payload = resp.json()
                text = payload["choices"][0]["message"]["content"]
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                exc_name = type(exc).__name__
                self._log(
                    task, tier, {}, elapsed, attempt, error=f"malformed payload: {exc_name}"
                )
                raise LLMError(
                    f"LM Studio returned a malformed payload for task {task!r}: {exc_name}"
                ) from exc
            self._log(
                task, tier, payload.get("usage", {}), elapsed, attempt
            )
            try:
                data = json.loads(text)
                errors = validate_json(data, schema)
            except json.JSONDecodeError:
                errors = [f"output was not valid JSON: {text[:120]!r}"]
                data = None
            if data is not None and not errors:
                return data
            prompt = (
                f"{user}\n\nYour previous output failed validation:\n- "
                + "\n- ".join(errors)
            )
        raise LLMOutputError(f"schema errors after retry for task {task!r}: {errors}")

    def _log(
        self,
        task: str,
        tier: str,
        usage: dict,
        seconds: float,
        attempt: int = 1,
        error: str | None = None,
    ) -> None:
        """Every call is logged, including HTTP failures (spec §10)."""
        record = {
            "ts": time.time(),
            "provider": self.name,
            "task": task,
            "tier": tier,
            "model": self.model,
            "attempt": attempt,
            "seconds": round(seconds, 2),
            "error": error,
            "usage": usage,
        }
        log_dir = paths.llm_cache_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "calls.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
