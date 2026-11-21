from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SYNTEGRA_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "staging", "prod"] = "dev"
    # mysql+asyncmy://user:pass@writer:3306/  (prod: RDS for MySQL 9.7 LTS Multi-AZ writer endpoint)
    database_url: str = "sqlite+aiosqlite:///./syntegra-dev.db"
    database_reader_url: str | None = None  # Multi-AZ cluster reader endpoint for dashboards/search
    residency_region: str = "af-south-1"
    sql_echo: bool = False
    # Dev-only header authentication. Never enabled outside dev/test (see web.auth).
    dev_auth: bool = True
    idempotency_ttl_hours: int = 24

    @property
    def is_mysql(self) -> bool:
        return self.database_url.startswith("mysql")


@lru_cache
def get_settings() -> Settings:
    return Settings()
