# ADR-007: Sign-in and tokens

**Status:** Accepted 2026-11-21 (development login). The OIDC + MFA sign-in is still open (ADR-005).

## Context
Phase 0 authenticated requests with `X-Dev-User-Id` / `X-Tenant-Id` headers: any caller could claim to
be anyone. CEDA §8.3 and §10.1 require OIDC/OAuth 2.1 with short-lived access tokens (5-15 min), rotating
refresh tokens, MFA by role and risk, and server-side sessions for the web.

## Decision
Split **proving identity** from **holding a session**. Syntegra always issues and verifies its own
tokens; only the proof that comes before issuance differs by environment.

| Step | Now | Next |
|---|---|---|
| Prove identity | `POST /v1/auth/dev-login` (organisation code + login name; **dev/test only**) | OIDC authorization code + PKCE with the federated IdP (Entra ID, Google, government IdP), MFA enforced by role |
| Issue tokens | `iam.auth._open_session` | unchanged |
| Every request | `Authorization: Bearer` → `verify_access_token` → `check_session` | unchanged |

**Access token:** ES256 JWT, `typ: at+jwt` (RFC 9068). Lifetime 10 minutes (configurable 5–15). Claims:
`iss aud sub tid sid jti iat nbf exp amr`. Only ES256 is accepted, so `alg: none` and HMAC key confusion
are rejected (tests cover both).

**Refresh token:** an opaque random value (`srt_` + 256 bits). Only its SHA-256 is stored
(`iam.refresh_token`). It is rotated on every use, with a 12-hour idle limit and a 7-day absolute session
limit. **Reusing a spent refresh token revokes the whole session** (OAuth 2.1 BCP theft detection).
Rotation takes a row lock, so concurrent refreshes serialise.

**Immediate revocation:** every request checks `iam.auth_session` (one primary-key read inside the request
transaction). Sign-out, deactivating a user and reuse detection therefore take effect at once, not when
the access token expires.

**Keys:** the signing key comes from `SYNTEGRA_TOKEN_SIGNING_KEY_PEM` (EC P-256). Previous public keys in
`SYNTEGRA_TOKEN_VERIFY_KEYS_PEM` keep verifying during rotation, and all are published at
`/.well-known/jwks.json`. Dev/test generate an ephemeral key when none is set. **Staging and prod refuse
to start without a key, and refuse to start with the dev login enabled.** Outside dev/test the dev-login
route is not registered at all.

**Active scope:** sent per request in `X-Scope-Node-Id`. It is not baked into the token, so switching scope
needs no new token. If absent, the tenant root is used when the user holds a grant there, otherwise the
first grant's scope.

**Other rules:**
* **Idempotency-Key:** the `/v1/auth/*` endpoints are exempt, as OAuth token endpoints are. Responses carry
  `Cache-Control: no-store`.
* **Failed sign-in:** returns the same `sign-in failed` message whether the organisation, user or status is
  wrong (no account enumeration).
* **Audit and events:** sign-in, refresh and sign-out are audited on the tenant's hash chain and emit
  `IAM.SESSION_STARTED` / `IAM.SESSION_REVOKED`.

## Open
* OIDC + PKCE, MFA (passkeys/TOTP), adaptive step-up, SCIM (ADR-005).
* **Signing key in AWS KMS** (asymmetric ECC_NIST_P256, so the private key never leaves KMS), replacing the
  PEM setting before production.
* Rate limiting of `/v1/auth/*` at the WAF and in the app.
* Web BFF: an HttpOnly cookie session for the browser UI (§10.1 "server-side session for web").
