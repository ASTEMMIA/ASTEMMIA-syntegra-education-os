"""Human verification for public forms (ADR-015).

Production uses a "siteverify" provider: Cloudflare Turnstile, hCaptcha and Google reCAPTCHA all
take a POST of ``secret`` + ``response`` (+ ``remoteip``) and answer ``{"success": true|false}``.
Dev/test use ``DevCaptcha``, which accepts only the token ``dev-ok``. Production refuses to start
without a real provider.
"""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

from syntegra.platform.settings import Settings

log = logging.getLogger(__name__)
DEV_TOKEN = "dev-ok"  # noqa: S105 - a public test token, not a secret


class CaptchaVerifier(Protocol):
    async def verify(self, token: str | None, remote_ip: str | None) -> bool: ...


class DevCaptcha:
    async def verify(self, token: str | None, remote_ip: str | None) -> bool:
        return token == DEV_TOKEN


class SiteVerifyCaptcha:
    def __init__(
        self,
        url: str,
        secret: str,
        timeout: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url, self._secret, self._timeout = url, secret, timeout
        self._transport = transport  # tests inject a mock transport

    async def verify(self, token: str | None, remote_ip: str | None) -> bool:
        if not token or len(token) > 4096:
            return False
        data = {"secret": self._secret, "response": token}
        if remote_ip:
            data["remoteip"] = remote_ip
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                r = await client.post(self._url, data=data)
                r.raise_for_status()
                return bool(r.json().get("success"))
        except httpx.HTTPError, ValueError:
            # Fail closed: if the provider cannot be reached, the form is not accepted.
            log.warning("captcha provider unavailable")
            return False


def build_captcha(settings: Settings) -> CaptchaVerifier:
    if settings.captcha_provider == "siteverify":
        if not (settings.captcha_verify_url and settings.captcha_secret):
            raise RuntimeError(
                "SYNTEGRA_CAPTCHA_VERIFY_URL and SYNTEGRA_CAPTCHA_SECRET are required"
            )
        return SiteVerifyCaptcha(
            settings.captcha_verify_url, settings.captcha_secret.get_secret_value()
        )
    if settings.env not in ("dev", "test"):
        raise RuntimeError(
            "a real CAPTCHA provider is required outside dev/test (SYNTEGRA_CAPTCHA_PROVIDER)"
        )
    return DevCaptcha()
