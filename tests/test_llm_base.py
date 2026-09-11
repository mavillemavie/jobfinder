import pytest

from jobfinder.llm.base import LLMError, validate_json
from jobfinder.llm.fake import FakeLLM

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def test_validate_json_reports_errors() -> None:
    assert validate_json({"ok": True}, SCHEMA) == []
    errors = validate_json({"ok": "yes"}, SCHEMA)
    assert errors and "ok" in errors[0]


def test_fake_llm_returns_canned_and_records_calls() -> None:
    llm = FakeLLM({"t": {"ok": True}, "seq": [{"ok": True}, {"ok": False}]})
    assert llm.complete_json(task="t", system="s", user="u", schema=SCHEMA)["ok"] is True
    assert llm.complete_json(task="seq", system="s", user="u", schema=SCHEMA)["ok"] is True
    assert llm.complete_json(task="seq", system="s", user="u", schema=SCHEMA)["ok"] is False
    assert llm.calls[0]["task"] == "t" and llm.calls[0]["user"] == "u"


def test_fake_llm_callable_and_missing() -> None:
    llm = FakeLLM({"echo": lambda user: {"ok": user == "hi"}})
    assert llm.complete_json(task="echo", system="", user="hi", schema=SCHEMA)["ok"] is True
    with pytest.raises(LLMError):
        llm.complete_json(task="nope", system="", user="", schema=SCHEMA)
