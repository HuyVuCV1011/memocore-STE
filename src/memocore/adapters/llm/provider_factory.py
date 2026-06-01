from __future__ import annotations

from memocore.adapters.llm.base import (
    ChatRequest,
    ChatResponse,
    ExtractionError,
    ModelProvider,
    ProviderInfo,
    StructuredOutputMode,
)
from memocore.adapters.llm.ollama_provider import OllamaProvider
from memocore.adapters.llm.openai_provider import OpenAICompatibleProvider
from memocore.config import FallbackConfig, ModelConfig


PROVIDER_DEFAULTS = {
    "ollama": ("http://127.0.0.1:11434", "qwen3:4b", StructuredOutputMode.JSON_MODE),
    "openai": ("https://api.openai.com/v1", "gpt-4.1-nano", StructuredOutputMode.JSON_SCHEMA),
    "deepseek": ("https://api.deepseek.com", "deepseek-chat", StructuredOutputMode.JSON_MODE),
    "openrouter": (
        "https://openrouter.ai/api/v1",
        "meta-llama/llama-3.3-70b-instruct:free",
        StructuredOutputMode.JSON_MODE,
    ),
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", StructuredOutputMode.JSON_MODE),
    "gemini": (
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "gemini-2.5-flash",
        StructuredOutputMode.JSON_MODE,
    ),
}


def create_provider(config: ModelConfig) -> ModelProvider:
    provider_name = config.provider.lower()
    base_url, default_model, default_mode = PROVIDER_DEFAULTS.get(
        provider_name,
        (config.base_url, config.name, StructuredOutputMode.PROMPT_ONLY),
    )
    model_name = config.name or default_model
    mode = _structured_output_mode(config.structured_output_mode, default_mode)

    if provider_name == "ollama":
        return OllamaProvider(
            base_url=config.base_url or base_url,
            model=model_name,
            timeout=config.timeout_seconds,
        )

    if provider_name in {"openai", "deepseek", "openrouter", "groq", "gemini"}:
        if not config.api_key:
            raise ValueError(f"{provider_name} provider requires MODEL_API_KEY")
        return OpenAICompatibleProvider(
            provider_name=provider_name,
            base_url=config.base_url or base_url,
            model=model_name,
            api_key=config.api_key,
            timeout=config.timeout_seconds,
            structured_output_mode=mode,
        )

    raise ValueError(f"Unsupported model provider: {config.provider}")


def create_provider_with_fallback(
    model_config: ModelConfig, fallback_config: FallbackConfig
) -> ModelProvider:
    primary = create_provider(model_config)
    if not fallback_config.provider:
        return primary
    fallback = create_provider(
        ModelConfig(
            provider=fallback_config.provider,
            name=fallback_config.name or "",
            base_url=fallback_config.base_url,
            api_key=fallback_config.api_key,
            timeout_seconds=model_config.timeout_seconds,
            temperature=model_config.temperature,
            structured_output_mode=fallback_config.structured_output_mode,
        )
    )
    return FallbackProvider(primary, fallback)


class FallbackProvider:
    def __init__(self, primary: ModelProvider, fallback: ModelProvider):
        self.primary = primary
        self.fallback = fallback

    @property
    def info(self) -> ProviderInfo:
        return self.primary.info

    async def health_check(self) -> bool:
        return await self.primary.health_check() or await self.fallback.health_check()

    async def chat(self, request: ChatRequest) -> ChatResponse:
        try:
            return await self.primary.chat(request)
        except ExtractionError:
            return await self.fallback.chat(request)

    @property
    def providers(self) -> tuple[ModelProvider, ...]:
        return (self.primary, self.fallback)


def _structured_output_mode(value: str, default: StructuredOutputMode) -> StructuredOutputMode:
    if value == "auto":
        return default
    return StructuredOutputMode(value)
