from types import SimpleNamespace

import anthropic
import httpx
import pytest

from jobfinder.llm.anthropic_api import AnthropicProvider
from jobfinder.llm.base import LLMError, LLMOutputError

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


class _Messages:
    def __init__(self, texts, stop="end_turn"):
        self.texts, self.stop, self.calls = list(texts), stop, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = self.texts.pop(0)
        return SimpleNamespace(
            stop_reason=self.stop,
            content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            model=kwargs["model"],
        )


def _client(texts, stop="end_turn"):
    m = _Messages(texts, stop)
    return SimpleNamespace(messages=m, beta=SimpleNamespace(messages=m)), m


def test_fast_tier_uses_sonnet_and_structured_output(home) -> None:
    client, m = _client(['{"ok": true}'])
    out = AnthropicProvider(client=client).complete_json(
        task="t", system="S", user="U", schema=SCHEMA, tier="fast"
    )
    assert out == {"ok": True}
    call = m.calls[0]
    assert call["model"] == "claude-sonnet-5" and call["system"] == "S"
    assert call["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}
    assert call["output_config"]["effort"] == "low" and call["thinking"] == {"type": "adaptive"}
    assert call["messages"] == [{"role": "user", "content": "U"}]
    assert "fallbacks" not in call


def test_strong_tier_uses_opus_with_server_side_fallback(home) -> None:
    client, m = _client(['{"ok": true}'])
    AnthropicProvider(client=client).complete_json(
        task="t", system="S", user="U", schema=SCHEMA, tier="strong"
    )
    call = m.calls[0]
    assert call["model"] == "claude-opus-5" and call["output_config"]["effort"] == "high"
    assert call["betas"] == ["server-side-fallback-2026-07-01"] and call["fallbacks"] == "default"


def test_refusal_raises(home) -> None:
    client, _ = _client(['{"ok": true}'], stop="refusal")
    with pytest.raises(LLMError):
        AnthropicProvider(client=client).complete_json(
            task="t", system="S", user="U", schema=SCHEMA
        )


def test_non_json_text_is_retried_once(home) -> None:
    client, m = _client(["not json", '{"ok": true}'])
    out = AnthropicProvider(client=client).complete_json(
        task="t", system="S", user="U", schema=SCHEMA
    )
    assert out == {"ok": True}
    assert "not valid JSON" in m.calls[1]["messages"][0]["content"]
    client2, _ = _client(["nope", "still nope"])
    with pytest.raises(LLMOutputError):
        AnthropicProvider(client=client2).complete_json(
            task="t", system="S", user="U", schema=SCHEMA
        )


def test_refusal_is_logged(home) -> None:
    import json

    from jobfinder import paths

    client, _ = _client(['{"ok": true}'], stop="refusal")
    with pytest.raises(LLMError):
        AnthropicProvider(client=client).complete_json(
            task="t", system="S", user="U", schema=SCHEMA
        )
    calls_path = paths.llm_cache_dir() / "calls.jsonl"
    last = json.loads(calls_path.read_text().strip().splitlines()[-1])
    assert last["error"] == "refusal" and last["provider"] == "anthropic"


def test_repair_retry_then_error(home) -> None:
    client, m = _client(['{"ok": "nope"}', '{"ok": true}'])
    out = AnthropicProvider(client=client).complete_json(
        task="t", system="S", user="U", schema=SCHEMA
    )
    assert out == {"ok": True}
    assert "failed validation" in m.calls[1]["messages"][0]["content"]
    client2, _ = _client(['{"ok": "a"}', '{"ok": "b"}'])
    with pytest.raises(LLMOutputError):
        AnthropicProvider(client=client2).complete_json(
            task="t", system="S", user="U", schema=SCHEMA
        )


def test_sdk_error_becomes_llm_error(home) -> None:
    import json

    from jobfinder import paths

    class _FailingMessages:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))

    m = _FailingMessages()
    client = SimpleNamespace(messages=m, beta=SimpleNamespace(messages=m))
    with pytest.raises(LLMError) as exc_info:
        AnthropicProvider(client=client).complete_json(
            task="t", system="S", user="U", schema=SCHEMA
        )
    assert not isinstance(exc_info.value, type(LLMOutputError))
    assert "APIConnectionError" in str(exc_info.value)
    calls_path = paths.llm_cache_dir() / "calls.jsonl"
    last = json.loads(calls_path.read_text().strip().splitlines()[-1])
    assert last["error"].startswith("APIConnectionError")


def test_no_text_block_raises_llm_error(home) -> None:
    class _NoTextMessages:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                stop_reason="max_tokens",
                content=[],
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                model=kwargs["model"],
            )

    m = _NoTextMessages()
    client = SimpleNamespace(messages=m, beta=SimpleNamespace(messages=m))
    with pytest.raises(LLMError) as exc_info:
        AnthropicProvider(client=client).complete_json(
            task="t", system="S", user="U", schema=SCHEMA
        )
    assert not isinstance(exc_info.value, type(LLMOutputError))
    assert "no text output" in str(exc_info.value)
    assert m.calls.__len__() == 1
