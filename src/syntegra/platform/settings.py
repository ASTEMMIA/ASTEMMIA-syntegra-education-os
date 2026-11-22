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

    # Public forms (ADR-015). Production needs a real CAPTCHA and email sender.
    # "siteverify" works with Cloudflare Turnstile, hCaptcha and Google reCAPTCHA: all accept a
    # POST of secret + response and return {"success": true|false}.
    captcha_provider: Literal["dev", "siteverify"] = "dev"
    captcha_verify_url: str | None = (
        None  # e.g. https://challenges.cloudflare.com/turnstile/v0/siteverify
    )
    captcha_secret: SecretStr | None = None
    captcha_site_key: str | None = None  # public; handed to the web form
    email_sender: Literal["dev", "ses"] = "dev"
    email_from: str = "no-reply@syntegra.education"
    # Amazon SES (ADR-017). The From address (or its domain) must be a verified SES identity in
    # this region. Credentials come from the AWS chain (ECS task role), never from settings.
    ses_region: str | None = None  # default: residency_region (af-south-1, Cape Town)
    ses_configuration_set: str | None = None  # bounce/complaint events and reputation metrics
    # SQS queue that receives the configuration set's events through SNS (ADR-018).
    ses_events_queue_url: str | None = None
    public_base_url: str = "http://localhost:8000"  # links in emails point here
    idempotency_ttl_hours: int = 24

    # Single sign-on (ADR-016). Identity providers are configured per organisation in
    # auth.identity_providers; their client secrets live only here, as a JSON object keyed by
    # each provider's client_secret_ref, e.g. {"ABIA_ENTRA": "..."} (from Secrets Manager).
    oidc_client_secrets: dict[str, SecretStr] = {}
    # Where identity providers send the browser back. Register this exact URL with each one.
    oidc_redirect_uri: str | None = None  # default: public_base_url + /v1/auth/oidc/callback
    authenticator_label: str = "Syntegra Education"  # shown in the authenticator app

    @property
    def redirect_uri(self) -> str:
        return self.oidc_redirect_uri or self.public_base_url.rstrip("/") + "/v1/auth/oidc/callback"

    @property
    def is_mysql(self) -> bool:
        return self.database_url.startswith("mysql")


@lru_cache
def get_settings() -> Settings:
    return Settings()
