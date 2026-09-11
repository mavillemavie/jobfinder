import os

from jobfinder.llm import get_llm
from jobfinder.llm.cache import CachedLLM
from jobfinder.llm.claude_code import ClaudeCodeProvider
from jobfinder.llm.fake import FakeLLM
from jobfinder.settings import Settings

SCHEMA = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}


def test_cache_hits_on_identical_inputs(home) -> None:
    inner = FakeLLM({"t": [{"n": 1}, {"n": 2}]})
    llm = CachedLLM(inner)
    assert llm.complete_json(task="t", system="s", user="u", schema=SCHEMA) == {"n": 1}
    assert llm.complete_json(task="t", system="s", user="u", schema=SCHEMA) == {"n": 1}
    assert (llm.hits, llm.misses) == (1, 1) and len(inner.calls) == 1
    assert llm.complete_json(task="t", system="s", user="different", schema=SCHEMA) == {"n": 2}
    llm.bypass_next()
    inner.responses["t"] = [{"n": 3}]
    assert llm.complete_json(task="t", system="s", user="u", schema=SCHEMA) == {"n": 3}


def test_get_llm_picks_provider(home) -> None:
    fake = get_llm(Settings(_env_file=None, llm_provider="fake"))
    assert isinstance(fake, CachedLLM) and isinstance(fake.inner, FakeLLM)
    cc = get_llm(Settings(_env_file=None), cache=False)
    assert isinstance(cc, ClaudeCodeProvider)
    assert isinstance(get_llm(Settings(_env_file=None), provider="fake", cache=False), FakeLLM)


def test_corrupt_cache_entry_is_treated_as_miss(home) -> None:
    inner = FakeLLM({"t": [{"n": 1}, {"n": 2}]})
    llm = CachedLLM(inner)

    # Compute the cache key path
    path = llm._key(task="t", tier="fast", system="s", user="u", schema=SCHEMA)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Write garbage to the cache file
    path.write_text("{not json")

    # Call complete_json and verify it treats the corrupt file as a miss
    result = llm.complete_json(task="t", system="s", user="u", schema=SCHEMA)

    # Should have called the inner provider once
    assert len(inner.calls) == 1
    # Should have recorded a miss (not a hit on the corrupt file)
    assert (llm.hits, llm.misses) == (0, 1)
    # Result should be the first valid response from FakeLLM
    assert result == {"n": 1}
    # The cache file should now contain valid JSON
    assert path.read_text() == '{"n": 1}'


def test_cache_write_is_atomic(home, monkeypatch) -> None:
    import os as os_module

    inner = FakeLLM({"t": [{"n": 42}]})
    llm = CachedLLM(inner)

    # Track calls to os.replace
    replace_calls = []
    original_replace = os.replace

    def tracked_replace(src, dst):
        replace_calls.append((src, dst))
        return original_replace(src, dst)

    monkeypatch.setattr(os_module, "replace", tracked_replace)

    # Call complete_json to trigger a cache write
    result = llm.complete_json(task="t", system="s", user="u", schema=SCHEMA)

    # Should have called os.replace exactly once
    assert len(replace_calls) == 1
    src, dst = replace_calls[0]
    # Destination should end with .json
    assert dst.endswith(".json")
    # Source should be the temp file (ends with .tmp)
    assert src.endswith(".tmp")

    # Verify no .tmp file remains in the cache directory
    cache_dir = llm.cache_dir / "t"
    tmp_files = list(cache_dir.glob("*.tmp"))
    assert len(tmp_files) == 0, f"Found leftover .tmp files: {tmp_files}"

    # Result should be correct
    assert result == {"n": 42}
