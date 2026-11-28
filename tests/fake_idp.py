"""A small but strict OpenID Provider for tests: discovery, JWKS, and a token endpoint that checks
PKCE, client authentication and the redirect URI, and signs RS256 ID tokens. Plug it into
``OidcClient(transport=idp.transport())``; ``authorize`` plays the person signing in."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa


class FakeIdP:
    def __init__(
        self,
        issuer: str = "https://login.idp.test/abia",
        client_id: str = "syntegra-abia",
        client_secret: str | None = "idp-s3cret",
    ) -> None:
        self.issuer = issuer
        self.client_id = client_id
        self.client_secret = client_secret
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "k1"
        self.codes: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []
        self.tamper: dict[str, Any] = {}  # claims to override in the next ID token

    # ------------------------------------------------------------------ provider side

    def provider_config(self, **overrides: Any) -> dict[str, Any]:
        return {
            "key": "entra",
            "name": "Abia State Microsoft 365",
            "issuer": self.issuer,
            "client_id": self.client_id,
            "client_secret_ref": "ABIA_ENTRA" if self.client_secret else None,
        } | overrides

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": self.issuer,
                    "authorization_endpoint": self.issuer + "/authorize",
                    "token_endpoint": self.issuer + "/token",
                    "jwks_uri": self.issuer + "/keys",
                },
            )
        if path.endswith("/keys"):
            jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
            return httpx.Response(200, json={"keys": [jwk | {"kid": self.kid, "use": "sig"}]})
        if path.endswith("/token") and request.method == "POST":
            return self._token(parse_qs(request.content.decode()))
        return httpx.Response(404)

    def _token(self, form: dict[str, list[str]]) -> httpx.Response:
        f = {k: v[0] for k, v in form.items()}
        grant = self.codes.pop(f.get("code", ""), None)  # codes work once
        bad = httpx.Response(400, json={"error": "invalid_grant"})
        if grant is None or f.get("grant_type") != "authorization_code":
            return bad
        if f.get("redirect_uri") != grant["redirect_uri"] or f.get("client_id") != self.client_id:
            return bad
        if self.client_secret and f.get("client_secret") != self.client_secret:
            return httpx.Response(401, json={"error": "invalid_client"})
        verifier = f.get("code_verifier", "")
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        if challenge != grant["code_challenge"]:
            return bad  # PKCE: only the party that started the flow can finish it
        now = int(time.time())
        claims = {
            "iss": self.issuer,
            "aud": self.client_id,
            "iat": now,
            "exp": now + 300,
            "nonce": grant["nonce"],
        } | grant["claims"]
        claims |= self.tamper
        token = jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": self.kid})
        return httpx.Response(200, json={"id_token": token, "token_type": "Bearer"})

    # ------------------------------------------------------------------ the person

    def authorize(self, authorization_url: str, **claims: Any) -> tuple[str, str]:
        """Sign in at the provider as ``claims`` (sub, email, amr, ...); return (state, code)."""
        q = {k: v[0] for k, v in parse_qs(urlsplit(authorization_url).query).items()}
        assert authorization_url.startswith(self.issuer + "/authorize?")
        assert q["response_type"] == "code" and q["client_id"] == self.client_id
        assert q["code_challenge_method"] == "S256" and "openid" in q["scope"].split()
        code = secrets.token_urlsafe(16)
        self.codes[code] = {
            "redirect_uri": q["redirect_uri"],
            "code_challenge": q["code_challenge"],
            "nonce": q["nonce"],
            "claims": claims,
        }
        return q["state"], code
