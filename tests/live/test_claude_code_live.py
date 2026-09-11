import shutil

import pytest

from jobfinder.llm.claude_code import ClaudeCodeProvider

pytestmark = pytest.mark.live


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")
def test_real_claude_returns_schema_json(home) -> None:
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    out = ClaudeCodeProvider().complete_json(
        task="live_probe", system="Output only JSON. Set ok to true.", user="Go.", schema=schema
    )
    assert out == {"ok": True}
