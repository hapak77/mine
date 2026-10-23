# -*- coding: utf-8 -*-
"""Настройки бота. Имена полей совпадают с переменными из .env.example."""

from urllib.parse import quote_plus

from pydantic import computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Заглушка из .env.example. Пока токен равен ей или пуст, оплата идёт в dry-run.
PLACEHOLDER_PROVIDER_TOKEN = "381764678:TEST:12345"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    BOT_TOKEN: str
    ADMIN_IDS: str = ""
    ADMIN_CHAT_ID: int | None = None

    USE_WEBHOOK: bool = False
    WEBHOOK_DOMAIN: str = ""
    WEBHOOK_PATH: str = "/webhook"
    WEBHOOK_SECRET: str = ""
    WEBHOOK_PORT: int = 8080

    POSTGRES_USER: str = "pdd_user"
    POSTGRES_PASSWORD: str = "pdd_secure_password"
    POSTGRES_DB: str = "pdd_database"
    POSTGRES_HOST: str = "db"
    POSTGRES_PORT: int = 5432

    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""

    PAYMENT_PROVIDER_TOKEN: str = ""
    ENABLE_TELEGRAM_STARS: bool = True

    LEAD_MAGNET_PATH: str = "data/master_pdd_guide.pdf"
    QUESTIONS_DATA_PATH: str = "data/parsed_tickets.json"

    WELCOME_VIDEO_NOTE_ID: str = ""
    DEMO_FAIL_VIDEO_NOTE_ID: str = ""

    @field_validator("ADMIN_CHAT_ID", mode="before")
    @classmethod
    def blank_admin_chat(cls, value):
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return value

    @field_validator(
        "WELCOME_VIDEO_NOTE_ID",
        "DEMO_FAIL_VIDEO_NOTE_ID",
        "WEBHOOK_SECRET",
        "WEBHOOK_DOMAIN",
        "WEBHOOK_PATH",
        "PAYMENT_PROVIDER_TOKEN",
        "ADMIN_IDS",
        mode="before",
    )
    @classmethod
    def none_to_empty(cls, value):
        if value is None:
            return ""
        return value

    @computed_field
    @property
    def redis_url(self) -> str:
        if self.REDIS_PASSWORD:
            password = quote_plus(self.REDIS_PASSWORD)
            return f"redis://:{password}@{self.REDIS_HOST}:{self.REDIS_PORT}/0"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"

    @property
    def database_url(self) -> str:
        user = quote_plus(self.POSTGRES_USER)
        password = quote_plus(self.POSTGRES_PASSWORD)
        return (
            f"postgresql+asyncpg://{user}:{password}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def admin_ids(self) -> set[int]:
        result: set[int] = set()
        for part in (self.ADMIN_IDS or "").split(","):
            part = part.strip()
            if part:
                result.add(int(part))
        return result

    @property
    def payment_dry_run(self) -> bool:
        token = (self.PAYMENT_PROVIDER_TOKEN or "").strip()
        return token == "" or token == PLACEHOLDER_PROVIDER_TOKEN


settings = Settings()
