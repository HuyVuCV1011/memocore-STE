import pytest
from pydantic import ValidationError

from memocore.config import Settings


def test_settings_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))

    settings = Settings(_env_file=None)

    assert settings.telegram_bot_token == "token"
    assert settings.database_path == tmp_path / "test.db"
    assert settings.model.provider == "ollama"
    assert settings.model.name == "qwen3:4b"


def test_settings_support_legacy_ollama_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.2:3b")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama")

    settings = Settings(_env_file=None)

    assert settings.model.name == "llama3.2:3b"
    assert settings.model.base_url == "http://ollama"


def test_settings_support_model_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    monkeypatch.setenv("MODEL_NAME", "gpt-4.1-nano")
    monkeypatch.setenv("MODEL_API_KEY", "key")

    settings = Settings(_env_file=None)

    assert settings.model.provider == "openai"
    assert settings.model.name == "gpt-4.1-nano"
    assert settings.model.api_key == "key"
    assert settings.model.base_url is None


def test_missing_token_fails(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
