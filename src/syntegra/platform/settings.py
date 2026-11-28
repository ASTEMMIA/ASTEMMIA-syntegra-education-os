from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SYNTEGRA_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "staging", "prod"] = "dev"
    # mysql+asyncmy://user:pass@writer:3306/  (prod: RDS for MySQL 9.7 LTS Multi-AZ writer endpoint)
    database_url: str = "sqlite+aiosqlite:///./syntegra-dev.db"
    database_reader_url: str | None = None  # Multi-AZ cluster reader endpoint for dashboards/search
    residency_region: str = "af-south-1"
    sql_echo: bool = False
    # Dev-only sign-in (POST /v1/auth/dev-login): proves identity by login name alone.
    # Never enabled outside dev/test; the app refuses to start otherwise (ADR-007).
    dev_auth: bool = True

    # Syntegra-issued tokens (§8.3, §10.1). Every environment uses the same verifier;
    # only the sign-in method that leads to issuance differs.
    token_issuer: str = "https://api.syntegra.education"  # noqa: S105 - a URL
    token_audience: str = "syntegra-api"  # noqa: S105 - an identifier
    access_token_ttl_seconds: int = Field(default=600, ge=300, le=900)  # 5-15 min (§10.1)
    refresh_token_ttl_seconds: int = 12 * 3600  # idle limit: each rotation extends it
    session_max_seconds: int = 7 * 24 * 3600  # absolute limit: re-authenticate after this
    # ES256 private key (PEM). Required outside dev/test; dev/test generate an ephemeral key.
    token_signing_key_pem: SecretStr | None = None
    # Public keys (PEM) still accepted for verification during key rotation.
    token_verify_keys_pem: list[str] = []

    # Field-level encryption of Restricted data (§8.1, ADR-008). 32-byte keys, base64.
    # Required outside dev/test; dev/test fall back to a fixed, public development key.
    field_kek_b64: SecretStr | None = None
    field_previous_keks_b64: list[str] = []  # still able to decrypt during rotation
    blind_index_key_b64: SecretStr | None = None
    idempotency_ttl_hours: int = 24

    @property
    def is_mysql(self) -> bool:
        return self.database_url.startswith("mysql")


@lru_cache
def get_settings() -> Settings:
    return Settings()
