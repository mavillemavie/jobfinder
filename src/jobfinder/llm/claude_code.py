from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from jobfinder import paths
from jobfinder.llm.base import LLMError, LLMOutputError, Tier, validate_json

MODEL_BY_TIER: dict[str, str] = {"fast": "claude-sonnet-5", "strong": "claude-opus-5"}
EFFORT_BY_TIER: dict[str, str] = {"fast": "low", "strong": "high"}

Runner = Callable[[list[str], str, int], subprocess.CompletedProcess]


def build_command(
    schema: dict[str, Any], tier: Tier, system: str, binary: str = "claude"
) -> list[str]:
    """The prompt itself is passed on stdin so variadic flags cannot swallow it."""
    return [
        binary, "-p",
        "--no-session-persistence",
        "--setting-sources", "",
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--model", MODEL_BY_TIER[tier],
        "--effort", EFFORT_BY_TIER[tier],
        "--tools", "",
        "--system-prompt", system,
    ]


def parse_envelope(stdout: str) -> dict[str, Any]:
    try:
        env = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise LLMError(f"claude -p returned non-JSON: {stdout[:200]!r}") from exc
    if env.get("is_error"):
        raise LLMError(f"claude -p error: {env.get('result')!r}")
    structured = env.get("structured_output")
    if isinstance(structured, dict):
        return structured
    result = env.get("result")
    if isinstance(result, str):
        try:
            return json.loads(result)
        except json.JSONDecodeError as exc:
            raise LLMOutputError(f"result is not JSON: {result[:200]!r}") from exc
    raise LLMOutputError("envelope has neither structured_output nor a JSON result")


def _default_runner(cmd: list[str], stdin: str, timeout: int) -> subprocess.CompletedProcess:
    workdir = paths.llm_workdir()
    workdir.mkdir(parents=True, exist_ok=True)
    # Windows: npm installs the CLI as claude.cmd, and CreateProcess only finds .exe by bare
    # name — resolve through PATH/PATHEXT first. No-op where the binary is a plain executable.
    resolved = shutil.which(cmd[0]) or cmd[0]
    return subprocess.run(
        [resolved, *cmd[1:]], input=stdin, capture_output=True, text=True, encoding="utf-8",
        timeout=timeout, cwd=workdir, check=False,
    )


@dataclass
class ClaudeCodeProvider:
    name: str = "claude_code"
    binary: str = "claude"
    timeout_s: int = 600
    runner: Runner | None = None
    last_usage: dict[str, Any] = field(default_factory=dict)

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
        cmd = build_command(schema, tier, system, self.binary)
        prompt = user
        errors: list[str] = []
        for attempt in (1, 2):
            started = time.monotonic()
            try:
                proc = (self.runner or _default_runner)(cmd, prompt, self.timeout_s)
            except subprocess.TimeoutExpired as exc:
                self._log(task, tier, attempt, time.monotonic() - started, "", error="timeout")
                raise LLMError(f"claude -p timed out after {self.timeout_s}s") from exc
            exit_error = (
                None if proc.returncode == 0 else f"exit {proc.returncode}: {proc.stderr[:500]}"
            )
            self._log(
                task, tier, attempt, time.monotonic() - started, proc.stdout, error=exit_error
            )
            if exit_error is not None:
                raise LLMError(f"claude -p {exit_error}")
            try:
                data = parse_envelope(proc.stdout)
            except LLMOutputError as exc:
                errors = [str(exc)]
            else:
                errors = validate_json(data, schema)
                if not errors:
                    return data
            prompt = (
                f"{user}\n\nYour previous output failed validation:\n- "
                + "\n- ".join(errors)
                + "\nReturn only JSON that matches the schema exactly."
            )
        raise LLMOutputError(f"schema errors after retry for task {task!r}: {errors}")

    def _log(
        self,
        task: str,
        tier: str,
        attempt: int,
        seconds: float,
        stdout: str,
        error: str | None = None,
    ) -> None:
        try:
            env = json.loads(stdout) if stdout else {}
        except json.JSONDecodeError:
            env = {}
        self.last_usage = env.get("usage", {}) or {}
        record = {
            "ts": time.time(), "provider": self.name, "task": task, "tier": tier,
            "model": MODEL_BY_TIER[tier], "attempt": attempt, "seconds": round(seconds, 2),
            "usage": self.last_usage, "cost_usd": env.get("total_cost_usd"), "error": error,
        }
        log_dir = paths.llm_cache_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "calls.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
