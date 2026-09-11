from __future__ import annotations

from jobfinder.llm.base import LLMProvider
from jobfinder.settings import Settings


def get_llm(settings: Settings, *, cache: bool = True, provider: str | None = None) -> LLMProvider:
    name = provider or settings.llm_provider
    if name == "fake":
        from jobfinder.llm.fake import FakeLLM

        llm: LLMProvider = FakeLLM()
    elif name == "anthropic":
        from jobfinder.llm.anthropic_api import AnthropicProvider

        llm = AnthropicProvider(api_key=settings.anthropic_api_key)
    elif name == "lmstudio":
        from jobfinder.llm.lmstudio import LMStudioProvider

        llm = LMStudioProvider(settings.lmstudio_base_url, settings.lmstudio_model)
    else:
        from jobfinder.llm.claude_code import ClaudeCodeProvider

        llm = ClaudeCodeProvider()
    if cache:
        from jobfinder.llm.cache import CachedLLM

        return CachedLLM(llm)
    return llm
