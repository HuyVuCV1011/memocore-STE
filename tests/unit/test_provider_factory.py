import pytest

from memocore.adapters.llm.ollama_provider import OllamaProvider
from memocore.adapters.llm.openai_provider import OpenAICompatibleProvider
from memocore.adapters.llm.provider_factory import (
    FallbackProvider,
    create_provider,
    create_provider_with_fallback,
)
from memocore.config import FallbackConfig, ModelConfig


def test_create_ollama_provider():
    config = ModelConfig(provider="ollama")
    provider = create_provider(config)
    assert isinstance(provider, OllamaProvider)


def test_create_openai_provider():
    config = ModelConfig(provider="openai", api_key="sk-test")
    provider = create_provider(config)
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.info.provider_name == "openai"
    assert provider.base_url == "https://api.openai.com/v1"


def test_create_groq_provider():
    config = ModelConfig(provider="groq", api_key="gsk-test")
    provider = create_provider(config)
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.info.provider_name == "groq"


def test_create_unsupported_raises():
    config = ModelConfig(provider="unknown")
    with pytest.raises(ValueError, match="Unsupported model provider"):
        create_provider(config)


def test_openai_without_key_raises():
    config = ModelConfig(provider="openai", api_key=None)
    with pytest.raises(ValueError, match="requires MODEL_API_KEY"):
        create_provider(config)


def test_auto_structured_output_mode():
    config = ModelConfig(provider="openai", api_key="sk-test", structured_output_mode="auto")
    provider = create_provider(config)
    # OpenAI default is JSON_SCHEMA
    from memocore.adapters.llm.base import StructuredOutputMode
    assert provider.info.supports_structured_output == StructuredOutputMode.JSON_SCHEMA


def test_fallback_provider_creation():
    model_config = ModelConfig(provider="ollama")
    fallback_config = FallbackConfig(provider="openai", api_key="sk-test")
    provider = create_provider_with_fallback(model_config, fallback_config)
    assert isinstance(provider, FallbackProvider)
    assert isinstance(provider.primary, OllamaProvider)
    assert isinstance(provider.fallback, OpenAICompatibleProvider)
