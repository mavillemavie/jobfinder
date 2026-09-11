from jobfinder.settings import Settings


def test_defaults_without_env(monkeypatch) -> None:
    for var in ("LLM_PROVIDER", "ADZUNA_APP_ID", "ADZUNA_APP_KEY"):
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)
    assert s.llm_provider == "claude_code"
    assert s.has("adzuna_app_id", "adzuna_app_key") is False


def test_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("ADZUNA_APP_ID", "id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "key")
    s = Settings(_env_file=None)
    assert s.llm_provider == "fake"
    assert s.has("adzuna_app_id", "adzuna_app_key") is True
