from pathlib import Path

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelConfig(BaseModel):
    provider: str = "ollama"
    name: str = "qwen3:14b"
    base_url: str | None = None
    api_key: str | None = None
    timeout_seconds: float = 60.0
    temperature: float = 0.0
    structured_output_mode: str = "auto"


class FallbackConfig(BaseModel):
    provider: str | None = None
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    structured_output_mode: str = "auto"


class Settings(BaseSettings):
    telegram_bot_token: str
    telegram_owner_id: int = Field(gt=0)
    database_path: Path = Path("data/memocore.db")
    log_level: str = "INFO"
    user_timezone: str = "UTC"
    morning_briefing_enabled: bool = True
    morning_briefing_time: str = "08:00"
    reminder_default_time: str = "09:00"
    weekly_review_enabled: bool = True
    weekly_review_weekday: int = 0
    weekly_review_time: str = "08:30"
    backup_enabled: bool = True
    backup_time: str = "03:30"
    backup_dir: Path = Path("backups")
    backup_retention_count: int = 14
    backup_retention_days: int | None = None
    proactive_nudges_enabled: bool = True
    proactive_nudge_interval_minutes: int = 60
    proactive_nudge_cooldown_hours: int = 24
    proactive_deadline_warning_hours: int = 4
    proactive_nudge_bundle_threshold: int = 2
    proactive_nudge_max_per_run: int = 5
    stale_followup_days: int = 3
    followup_nudge_window_start: str | None = None
    followup_nudge_window_end: str | None = None
    focus_window_start: str | None = None
    focus_window_end: str | None = None
    quiet_hours_start: str | None = "22:00"
    quiet_hours_end: str | None = "07:00"
    model: ModelConfig = Field(default_factory=ModelConfig)
    fallback: FallbackConfig = Field(default_factory=FallbackConfig)

    model_provider: str | None = Field(default=None, validation_alias="MODEL_PROVIDER", exclude=True)
    model_name: str | None = Field(default=None, validation_alias="MODEL_NAME", exclude=True)
    model_base_url: str | None = Field(default=None, validation_alias="MODEL_BASE_URL", exclude=True)
    model_api_key: str | None = Field(default=None, validation_alias="MODEL_API_KEY", exclude=True)
    model_timeout_seconds: float | None = Field(
        default=None, validation_alias="MODEL_TIMEOUT_SECONDS", exclude=True
    )
    model_temperature: float | None = Field(
        default=None, validation_alias="MODEL_TEMPERATURE", exclude=True
    )
    model_structured_output_mode: str | None = Field(
        default=None, validation_alias="MODEL_STRUCTURED_OUTPUT_MODE", exclude=True
    )
    fallback_provider: str | None = Field(
        default=None, validation_alias="MODEL_FALLBACK_PROVIDER", exclude=True
    )
    fallback_name: str | None = Field(
        default=None, validation_alias="MODEL_FALLBACK_NAME", exclude=True
    )
    fallback_base_url: str | None = Field(
        default=None, validation_alias="MODEL_FALLBACK_BASE_URL", exclude=True
    )
    fallback_api_key: str | None = Field(
        default=None, validation_alias="MODEL_FALLBACK_API_KEY", exclude=True
    )
    fallback_structured_output_mode: str | None = Field(
        default=None, validation_alias="MODEL_FALLBACK_STRUCTURED_OUTPUT_MODE", exclude=True
    )

    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY", exclude=True)
    gemini_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY", exclude=True)
    deepseek_api_key: str | None = Field(
        default=None, validation_alias="DEEPSEEK_API_KEY", exclude=True
    )
    openrouter_api_key: str | None = Field(
        default=None, validation_alias="OPENROUTER_API_KEY", exclude=True
    )
    groq_api_key: str | None = Field(default=None, validation_alias="GROQ_API_KEY", exclude=True)

    ollama_base_url: str | None = Field(
        default=None, validation_alias="OLLAMA_BASE_URL", exclude=True
    )
    ollama_model: str | None = Field(default=None, validation_alias="OLLAMA_MODEL", exclude=True)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    @model_validator(mode="after")
    def _apply_flat_model_env(self) -> "Settings":
        provider = self.model_provider or self.model.provider
        model_updates = {
            "provider": provider,
            "name": self.model_name or self.ollama_model,
            "base_url": self.model_base_url or self.ollama_base_url,
            "api_key": self.model_api_key or self.api_key_for_provider(provider),
            "timeout_seconds": self.model_timeout_seconds,
            "temperature": self.model_temperature,
            "structured_output_mode": self.model_structured_output_mode,
        }
        self.model = self.model.model_copy(
            update={key: value for key, value in model_updates.items() if value is not None}
        )
        fallback_updates = {
            "provider": self.fallback_provider,
            "name": self.fallback_name,
            "base_url": self.fallback_base_url,
            "api_key": self.fallback_api_key or self.api_key_for_provider(self.fallback_provider),
            "structured_output_mode": self.fallback_structured_output_mode,
        }
        self.fallback = self.fallback.model_copy(
            update={key: value for key, value in fallback_updates.items() if value is not None}
        )
        return self

    def api_key_for_provider(self, provider: str | None) -> str | None:
        keys = {
            "openai": self.openai_api_key,
            "gemini": self.gemini_api_key,
            "deepseek": self.deepseek_api_key,
            "openrouter": self.openrouter_api_key,
            "groq": self.groq_api_key,
        }
        return keys.get(provider or "")

    def with_model_override(self, provider: str | None = None, name: str | None = None) -> "Settings":
        active_provider = provider or self.model.provider
        provider_changed = provider is not None and provider != self.model.provider
        active_name = name if name is not None else ("" if provider_changed else self.model.name)
        active_base_url = None if provider_changed else self.model.base_url
        return self.model_copy(
            update={
                "model": self.model.model_copy(
                    update={
                        "provider": active_provider,
                        "name": active_name,
                        "base_url": active_base_url,
                        "api_key": self.api_key_for_provider(active_provider) or self.model_api_key,
                    }
                )
            }
        )


def get_settings() -> Settings:
    return Settings()
