"""Single sign-on through OpenID Connect (CEDA §8.3, ADR-016). No database access here.

Authorization-code flow with PKCE (S256), the profile OAuth 2.1 requires for every client.
Works with any certified OpenID Provider: Microsoft Entra ID, Google Workspace, Okta, Keycloak,
or a government identity service.

The ID token is checked in full: signature against the provider's published keys, issuer,
audience (and ``azp`` when several audiences), expiry, issue time and the ``nonce`` bound to this
browser's sign-in attempt. Provider responses are fetched with httpx; tests pass a mock transport.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from syntegra.core.errors import Unauthenticated, ValidationFailed

ID_TOKEN_ALGORITHMS = ("RS256", "PS256", "ES256")  # never "none", never HMAC
LEEWAY_SECONDS = 60
DISCOVERY_TTL_SECONDS = 3600
# amr values (RFC 8176) that show the provider itself checked a second factor. "sms" is not
# strong (§8.3), so a provider that reports it is never trusted for MFA.
STRONG_AMR = frozenset({"mfa", "hwk", "swk", "otp", "fpt", "face", "iris", "retina", "sc"})
WEAK_AMR = frozenset({"sms", "tel"})

FAILED = "single sign-on failed"


@dataclass(frozen=True, slots=True)
class IdentityProvider:
    """One identity provider an organisation lets its people sign in with.

    Held in configuration (``auth.identity_providers``) so it is inherited down the hierarchy:
    a state's Microsoft 365 tenant can serve every school under the state. The client secret is
    never in configuration: ``client_secret_ref`` names an entry of SYNTEGRA_OIDC_CLIENT_SECRETS.
    """

    key: str
    name: str
    issuer: str
    client_id: str
    discovery_url: str | None = None
    client_secret_ref: str | None = None
    scopes: tuple[str, ...] = ("openid", "email", "profile")
    trust_idp_mfa: bool = False  # accept the provider's own MFA (amr) instead of asking again
    link_by_email: bool = False  # first sign-in may match an existing user by verified email
    email_domains: tuple[str, ...] = ()  # domains this provider is authoritative for

    @property
    def discovery(self) -> str:
        return self.discovery_url or self.issuer.rstrip("/") + "/.well-known/openid-configuration"

    def public(self) -> dict[str, str]:
        return {"key": self.key, "name": self.name}


def parse_providers(value: Any) -> list[IdentityProvider]:
    """Read the ``auth.identity_providers`` setting. Bad entries fail loudly, not silently."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationFailed("auth.identity_providers must be a list")
    out: list[IdentityProvider] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            raise ValidationFailed("each identity provider must be an object")
        missing = [k for k in ("key", "name", "issuer", "client_id") if not raw.get(k)]
        if missing:
            raise ValidationFailed(f"identity provider is missing {', '.join(missing)}")
        issuer = str(raw["issuer"])
        if not issuer.startswith("https://"):
            raise ValidationFailed(f"identity provider {raw['key']}: issuer must use https")
        scopes = tuple(raw.get("scopes") or ("openid", "email", "profile"))
        if "openid" not in scopes:
            scopes = ("openid", *scopes)
        out.append(
            IdentityProvider(
                key=str(raw["key"]).lower(),
                name=str(raw["name"]),
                issuer=issuer,
                client_id=str(raw["client_id"]),
                discovery_url=raw.get("discovery_url"),
                client_secret_ref=raw.get("client_secret_ref"),
                scopes=scopes,
                trust_idp_mfa=bool(raw.get("trust_idp_mfa", False)),
                link_by_email=bool(raw.get("link_by_email", False)),
                email_domains=tuple(d.lower().lstrip("@") for d in raw.get("email_domains", ())),
            )
        )
    keys = [p.key for p in out]
    if len(keys) != len(set(keys)):
        raise ValidationFailed("identity provider keys must be unique")
    return out


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def new_pkce() -> tuple[str, str]:
    """(code_verifier, S256 code_challenge), RFC 7636."""
    verifier = _b64(secrets.token_bytes(32))
    return verifier, _b64(hashlib.sha256(verifier.encode("ascii")).digest())


def new_state() -> str:
    return _b64(secrets.token_bytes(32))


def new_nonce() -> str:
    return _b64(secrets.token_bytes(24))


def hash_state(state: str) -> bytes:
    return hashlib.sha256(state.encode("utf-8")).digest()


@dataclass(frozen=True, slots=True)
class Discovery:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


@dataclass(frozen=True, slots=True)
class IdTokenClaims:
    issuer: str
    subject: str
    email: str | None
    email_verified: bool
    name: str | None
    amr: tuple[str, ...]
    raw: Mapping[str, Any] = field(repr=False)

    @property
    def provider_mfa(self) -> bool:
        amr = set(self.amr)
        return bool(amr & STRONG_AMR) and not (amr & WEAK_AMR)


def subject_key(issuer: str, subject: str) -> str:
    """What is stored in app_user.idp_subject: ``sub`` is unique only within one issuer."""
    if not subject or len(subject) > 200:
        raise Unauthenticated(FAILED, next_action="Your identity provider sent an unusable id.")
    return hashlib.sha256(issuer.encode()).hexdigest()[:16] + ":" + subject


class OidcClient:
    """Talks to identity providers. Discovery documents and keys are cached for an hour."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 10.0):
        self._transport = transport
        self._timeout = timeout
        self._discovery: dict[str, tuple[float, Discovery]] = {}
        self._jwks: dict[str, tuple[float, dict[str, Any]]] = {}

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=self._timeout)

    async def _get_json(self, url: str) -> dict[str, Any]:
        try:
            async with self._http() as http:
                r = await http.get(url, headers={"Accept": "application/json"})
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise Unauthenticated(
                FAILED, next_action="The identity provider could not be reached. Try again."
            ) from exc
        if not isinstance(data, dict):
            raise Unauthenticated(FAILED)
        return data

    async def discover(self, idp: IdentityProvider) -> Discovery:
        hit = self._discovery.get(idp.discovery)
        if hit and time.monotonic() - hit[0] < DISCOVERY_TTL_SECONDS:
            return hit[1]
        doc = await self._get_json(idp.discovery)
        try:
            found = Discovery(
                issuer=str(doc["issuer"]),
                authorization_endpoint=str(doc["authorization_endpoint"]),
                token_endpoint=str(doc["token_endpoint"]),
                jwks_uri=str(doc["jwks_uri"]),
            )
        except KeyError as exc:
            raise Unauthenticated(FAILED) from exc
        # OIDC Discovery §4.3: the document must name the issuer we were configured with.
        if found.issuer.rstrip("/") != idp.issuer.rstrip("/"):
            raise Unauthenticated(
                FAILED, next_action="The identity provider's issuer does not match its settings."
            )
        for url in (found.authorization_endpoint, found.token_endpoint, found.jwks_uri):
            if not url.startswith("https://"):
                raise Unauthenticated(FAILED)
        self._discovery[idp.discovery] = (time.monotonic(), found)
        return found

    def authorization_url(
        self,
        idp: IdentityProvider,
        discovery: Discovery,
        *,
        redirect_uri: str,
        state: str,
        nonce: str,
        code_challenge: str,
        login_hint: str | None = None,
    ) -> str:
        params = {
            "response_type": "code",
            "client_id": idp.client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(idp.scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if login_hint:
            params["login_hint"] = login_hint
        sep = "&" if "?" in discovery.authorization_endpoint else "?"
        return discovery.authorization_endpoint + sep + urlencode(params)

    async def exchange_code(
        self,
        idp: IdentityProvider,
        discovery: Discovery,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        client_secret: str | None,
    ) -> str:
        """Swap the authorization code for tokens and return the ID token (still unverified)."""
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
            "client_id": idp.client_id,
        }
        if client_secret:
            form["client_secret"] = client_secret  # client_secret_post: Entra and Google accept it
        try:
            async with self._http() as http:
                r = await http.post(
                    discovery.token_endpoint, data=form, headers={"Accept": "application/json"}
                )
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise Unauthenticated(
                FAILED, next_action="The identity provider could not be reached. Try again."
            ) from exc
        if r.status_code != 200 or not isinstance(data, dict) or "id_token" not in data:
            raise Unauthenticated(FAILED, next_action="Start signing in again.")
        return str(data["id_token"])

    async def _keys(self, jwks_uri: str, *, refresh: bool = False) -> dict[str, Any]:
        hit = self._jwks.get(jwks_uri)
        if hit and not refresh and time.monotonic() - hit[0] < DISCOVERY_TTL_SECONDS:
            return hit[1]
        doc = await self._get_json(jwks_uri)
        self._jwks[jwks_uri] = (time.monotonic(), doc)
        return doc

    async def validate_id_token(
        self,
        idp: IdentityProvider,
        discovery: Discovery,
        id_token: str,
        *,
        nonce: str,
    ) -> IdTokenClaims:
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as exc:
            raise Unauthenticated(FAILED) from exc
        alg = header.get("alg")
        if alg not in ID_TOKEN_ALGORITHMS:
            raise Unauthenticated(FAILED)
        key = await self._signing_key(discovery.jwks_uri, header.get("kid"), str(alg))
        try:
            claims: dict[str, Any] = jwt.decode(
                id_token,
                key,
                algorithms=[str(alg)],
                audience=idp.client_id,
                issuer=discovery.issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": ["iss", "sub", "aud", "exp", "iat"]},
            )
        except jwt.PyJWTError as exc:
            raise Unauthenticated(FAILED, next_action="Start signing in again.") from exc
        aud = claims["aud"]
        if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != idp.client_id:
            raise Unauthenticated(FAILED)  # OIDC Core §3.1.3.7 rule 4-5
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise Unauthenticated(FAILED, next_action="Start signing in again.")
        email = claims.get("email") or None
        amr = claims.get("amr") or ()
        return IdTokenClaims(
            issuer=str(claims["iss"]),
            subject=str(claims["sub"]),
            email=str(email).strip().lower() if email else None,
            email_verified=claims.get("email_verified") in (True, "true"),
            name=claims.get("name"),
            amr=tuple(str(a) for a in amr) if isinstance(amr, Sequence) else (),
            raw=claims,
        )

    async def _signing_key(self, jwks_uri: str, kid: Any, alg: str) -> Any:
        for refresh in (False, True):  # a new kid means the provider rotated: fetch once more
            jwks = await self._keys(jwks_uri, refresh=refresh)
            for jwk in jwks.get("keys", []):
                if not isinstance(jwk, dict) or jwk.get("use", "sig") != "sig":
                    continue
                if kid is not None and jwk.get("kid") != kid:
                    continue
                try:
                    return jwt.PyJWK.from_json(json.dumps(jwk), algorithm=alg).key
                except jwt.PyJWTError:
                    continue
        raise Unauthenticated(FAILED)
