import json

import httpx
import respx

from jobfinder.llm.lmstudio import LMStudioProvider

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


@respx.mock
def test_lmstudio_json_mode(home) -> None:
    route = respx.post("http://localhost:1234/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
        )
    )
    out = LMStudioProvider("http://localhost:1234", "qwen").complete_json(
        task="t", system="S", user="U", schema=SCHEMA
    )
    assert out == {"ok": True}
    body = json.loads(route.calls[0].request.content)
    assert body["model"] == "qwen" and body["messages"][0] == {"role": "system", "content": "S"}
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA and body["temperature"] == 0


@respx.mock
def test_lmstudio_non_json_retry_and_http_error_logged(home) -> None:
    import pytest

    from jobfinder import paths
    from jobfinder.llm.base import LLMError, LLMOutputError

    route = respx.post("http://localhost:1234/v1/chat/completions").mock(side_effect=[
        httpx.Response(200, json={"choices": [{"message": {"content": "nope"}}]}),
        httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]}),
        httpx.Response(200, json={"choices": [{"message": {"content": "a"}}]}),
        httpx.Response(200, json={"choices": [{"message": {"content": "b"}}]}),
        httpx.Response(503),
    ])
    llm = LMStudioProvider("http://localhost:1234", "qwen")
    assert llm.complete_json(task="t", system="S", user="U", schema=SCHEMA) == {"ok": True}
    assert "not valid JSON" in json.loads(route.calls[1].request.content)["messages"][1]["content"]
    with pytest.raises(LLMOutputError):
        llm.complete_json(task="t", system="S", user="U", schema=SCHEMA)
    with pytest.raises(LLMError):
        llm.complete_json(task="t", system="S", user="U", schema=SCHEMA)
    last = json.loads((paths.llm_cache_dir() / "calls.jsonl").read_text().strip().splitlines()[-1])
    assert last["error"] and last["provider"] == "lmstudio"


@respx.mock
def test_lmstudio_malformed_payload_raises_llm_error(home) -> None:
    import pytest

    from jobfinder import paths
    from jobfinder.llm.base import LLMError

    route = respx.post("http://localhost:1234/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": []})
    )
    llm = LMStudioProvider("http://localhost:1234", "qwen")
    with pytest.raises(LLMError):
        llm.complete_json(task="t", system="S", user="U", schema=SCHEMA)
    assert len(route.calls) == 1
    last = json.loads((paths.llm_cache_dir() / "calls.jsonl").read_text().strip().splitlines()[-1])
    assert last["error"].startswith("malformed payload")
