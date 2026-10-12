"""Настройки сервиса из переменных окружения.

Все переменные перечислены в `.env.example` и в `environment:` сервиса `bot`
в `docker-compose.coolify.yml`. Значения читаются один раз (`get_settings()`);
в тестах кэш сбрасывается через `get_settings.cache_clear()`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_file_encoding="utf-8")

    bot_token: str | None = None
    telegram_webhook_secret: str | None = None
    public_base_url: str = "https://reg.alexshein.com"

    database_url: str = "postgresql+psycopg://bot:bot@localhost:5432/ai_native_bot"
    postgres_password: str | None = None

    admin_chat_ids: Annotated[list[int], NoDecode] = []
    privacy_url: str = "https://alexshein.com/privacy"
    site_webhook_secret: str | None = None

    llm_provider: Literal["deepseek", "anthropic", "stub"] = "stub"
    llm_api_key: str | None = None
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_timeout_seconds: float = 30

    setup_bot_on_start: bool = True
    log_level: str = "INFO"

    @field_validator("admin_chat_ids", mode="before")
    @classmethod
    def _split_admin_ids(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, int):
            return [value]
        if isinstance(value, str):
            return [int(part) for part in value.replace(";", ",").split(",") if part.strip()]
        return value

    @field_validator("bot_token", "telegram_webhook_secret", "site_webhook_secret", "llm_api_key", mode="before")
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def webhook_url(self) -> str:
        return self.public_base_url.rstrip("/") + "/tg/webhook"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
