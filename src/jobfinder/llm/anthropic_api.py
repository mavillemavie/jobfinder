from __future__ import annotations

import json
import time
from typing import Any

import anthropic

from jobfinder import paths
from jobfinder.llm.base import LLMError, LLMOutputError, Tier, validate_json
from jobfinder.llm.claude_code import EFFORT_BY_TIER, MODEL_BY_TIER

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    """Official Anthropic SDK. Used when LLM_PROVIDER=anthropic (API key or `ant auth login`)."""

    name = "anthropic"

    def __init__(self, api_key: str | None = None, client: Any = None) -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.client = client

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
        model = MODEL_BY_TIER[tier]
        prompt = user
        errors: list[str] = []
        for attempt in (1, 2):
            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": 16000,
                "system": system,
                "thinking": {"type": "adaptive"},
                "output_config": {
                    "effort": EFFORT_BY_TIER[tier],
                    "format": {"type": "json_schema", "schema": schema},
                },
                "messages": [{"role": "user", "content": prompt}],
            }
            started = time.monotonic()
            try:
                if tier == "strong":
                    kwargs["betas"] = [FALLBACK_BETA]
                    kwargs["fallbacks"] = "default"
                    response = self.client.beta.messages.create(**kwargs)
                else:
                    response = self.client.messages.create(**kwargs)
            except anthropic.APIError as exc:
                elapsed = time.monotonic() - started
                exc_msg = f"{type(exc).__name__}: {str(exc)[:200]}"
                self._log(task, tier, None, elapsed, attempt, error=exc_msg)
                raise LLMError(f"anthropic API error for task {task!r}: {exc_msg}") from exc
            elapsed = time.monotonic() - started
            refused = getattr(response, "stop_reason", None) == "refusal"
            self._log(
                task, tier, response, elapsed, attempt,
                error="refusal" if refused else None,
            )
            if refused:
                raise LLMError(f"model refused task {task!r}")
            stop_reason = getattr(response, "stop_reason", None)
            text = next(
                (b.text for b in response.content if getattr(b, "type", "") == "text"), None
            )
            if text is None:
                error_msg = f"no text block (stop_reason={stop_reason})"
                self._log(task, tier, response, elapsed, attempt, error=error_msg)
                raise LLMError(f"no text output for task {task!r} (stop_reason={stop_reason})")
            try:
                data = json.loads(text)
                errors = validate_json(data, schema)
            except json.JSONDecodeError:
                errors = [f"output was not valid JSON: {text[:120]!r}"]
                data = None
            if data is not None and not errors:
                return data
            prompt = (
                f"{user}\n\nYour previous output failed validation:\n- " + "\n- ".join(errors)
                + "\nReturn only JSON that matches the schema exactly."
            )
        raise LLMOutputError(f"schema errors after retry for task {task!r}: {errors}")

    def _log(
        self, task: str, tier: str, response: Any, seconds: float, attempt: int = 1,
        error: str | None = None,
    ) -> None:
        """Every call is logged, including refusals (spec §10)."""
        usage = getattr(response, "usage", None) if response is not None else None
        model = (
            getattr(response, "model", MODEL_BY_TIER[tier])
            if response is not None
            else MODEL_BY_TIER[tier]
        )
        record = {
            "ts": time.time(), "provider": self.name, "task": task, "tier": tier,
            "model": model, "attempt": attempt,
            "seconds": round(seconds, 2), "error": error,
            "usage": {
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
            },
        }
        log_dir = paths.llm_cache_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "calls.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
