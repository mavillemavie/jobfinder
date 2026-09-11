from __future__ import annotations

import json
import subprocess

import pytest

from jobfinder.llm.base import LLMError, LLMOutputError
from jobfinder.llm.claude_code import ClaudeCodeProvider, build_command, parse_envelope

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def _proc(stdout: str, code: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr=stderr)


def test_build_command_uses_tier_model_and_effort() -> None:
    cmd = build_command(SCHEMA, "fast", "SYS")
    assert cmd[:2] == ["claude", "-p"]
    assert "--bare" not in cmd
    assert "--no-session-persistence" in cmd
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-5"
    assert cmd[cmd.index("--effort") + 1] == "low"
    assert cmd[cmd.index("--system-prompt") + 1] == "SYS"
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == SCHEMA
    strong = build_command(SCHEMA, "strong", "SYS")
    assert strong[strong.index("--model") + 1] == "claude-opus-5"
    assert strong[strong.index("--effort") + 1] == "high"


def test_parse_envelope_prefers_structured_output() -> None:
    env = {"is_error": False, "result": "ignored", "structured_output": {"ok": True}}
    assert parse_envelope(json.dumps(env)) == {"ok": True}
    env2 = {"is_error": False, "result": '{"ok": false}'}
    assert parse_envelope(json.dumps(env2)) == {"ok": False}


def test_parse_envelope_raises_output_error_for_malformed_result() -> None:
    env = {"is_error": False, "result": "not json at all"}
    with pytest.raises(LLMOutputError):
        parse_envelope(json.dumps(env))


def test_parse_envelope_raises_output_error_for_neither_key() -> None:
    env = {"is_error": False}
    with pytest.raises(LLMOutputError):
        parse_envelope(json.dumps(env))


def test_parse_envelope_raises_llm_error_for_is_error() -> None:
    env = {"is_error": True, "result": "Not logged in"}
    with pytest.raises(LLMError):
        parse_envelope(json.dumps(env))


def test_provider_happy_path_records_call(home) -> None:
    calls: list[str] = []

    def runner(cmd, stdin, timeout):  # noqa: ANN001
        calls.append(stdin)
        return _proc(json.dumps({"is_error": False, "structured_output": {"ok": True}}))

    llm = ClaudeCodeProvider(runner=runner)
    out = llm.complete_json(task="t", system="S", user="hello", schema=SCHEMA, tier="fast")
    assert out == {"ok": True}
    assert calls == ["hello"]
    log = (home / "data" / "llm-cache" / "calls.jsonl").read_text().strip().splitlines()
    assert json.loads(log[-1])["task"] == "t"


def test_provider_retries_once_then_raises(home) -> None:
    outputs = iter([{"ok": "bad"}, {"ok": "still bad"}])

    def runner(cmd, stdin, timeout):  # noqa: ANN001
        return _proc(json.dumps({"is_error": False, "structured_output": next(outputs)}))

    llm = ClaudeCodeProvider(runner=runner)
    with pytest.raises(LLMOutputError):
        llm.complete_json(task="t", system="S", user="u", schema=SCHEMA)


def test_provider_repairs_on_second_try(home) -> None:
    outputs = iter([{"ok": "bad"}, {"ok": True}])
    seen: list[str] = []

    def runner(cmd, stdin, timeout):  # noqa: ANN001
        seen.append(stdin)
        return _proc(json.dumps({"is_error": False, "structured_output": next(outputs)}))

    out = ClaudeCodeProvider(runner=runner).complete_json(
        task="t", system="S", user="u", schema=SCHEMA
    )
    assert out == {"ok": True}
    assert "failed validation" in seen[1]


def test_malformed_result_is_retried_once(home) -> None:
    outputs = iter(
        [
            {"is_error": False, "result": "not json at all"},
            {"is_error": False, "structured_output": {"ok": True}},
        ]
    )
    seen: list[str] = []

    def runner(cmd, stdin, timeout):  # noqa: ANN001
        seen.append(stdin)
        return _proc(json.dumps(next(outputs)))

    out = ClaudeCodeProvider(runner=runner).complete_json(
        task="t", system="S", user="u", schema=SCHEMA
    )
    assert out == {"ok": True}
    assert "failed validation" in seen[1]


def test_malformed_twice_raises_output_error(home) -> None:
    def runner(cmd, stdin, timeout):  # noqa: ANN001
        return _proc(json.dumps({"is_error": False, "result": "not json at all"}))

    with pytest.raises(LLMOutputError):
        ClaudeCodeProvider(runner=runner).complete_json(
            task="t", system="S", user="u", schema=SCHEMA
        )


def test_failed_call_is_logged(home) -> None:
    def runner(cmd, stdin, timeout):  # noqa: ANN001
        return _proc("", code=1, stderr="boom")

    with pytest.raises(LLMError):
        ClaudeCodeProvider(runner=runner).complete_json(
            task="t", system="S", user="u", schema=SCHEMA
        )
    log = (home / "data" / "llm-cache" / "calls.jsonl").read_text().strip().splitlines()
    record = json.loads(log[-1])
    assert record["error"].startswith("exit 1")
    assert record["attempt"] == 1


def test_is_error_envelope_raises_llm_error_and_logs(home) -> None:
    def runner(cmd, stdin, timeout):  # noqa: ANN001
        return _proc(json.dumps({"is_error": True, "result": "Not logged in"}))

    with pytest.raises(LLMError) as exc_info:
        ClaudeCodeProvider(runner=runner).complete_json(
            task="t", system="S", user="u", schema=SCHEMA
        )
    assert not isinstance(exc_info.value, LLMOutputError)
    log = (home / "data" / "llm-cache" / "calls.jsonl").read_text().strip().splitlines()
    assert len(log) >= 1
