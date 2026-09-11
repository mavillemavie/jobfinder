from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from jobfinder import paths


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_provider: Literal["claude_code", "anthropic", "lmstudio", "fake"] = "claude_code"
    anthropic_api_key: str | None = None
    lmstudio_base_url: str = "http://localhost:1234"
    lmstudio_model: str = "local-model"

    adzuna_app_id: str | None = None
    adzuna_app_key: str | None = None
    reed_api_key: str | None = None
    jooble_api_key: str | None = None
    rapidapi_key: str | None = None

    apollo_api_key: str | None = None
    hunter_api_key: str | None = None
    serper_api_key: str | None = None

    gmail_user: str | None = None
    gmail_app_password: str | None = None
    digest_to: str | None = None

    voice_file: str = "~/work/references/voice.md"

    def has(self, *names: str) -> bool:
        return all(bool(getattr(self, n)) for n in names)


def get_settings() -> Settings:
    return Settings(_env_file=paths.home() / ".env")
