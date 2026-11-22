# ADR-016: Single sign-on (OpenID Connect) and multi-factor authentication

**Status:** Accepted 2026-11-22. Passkeys/FIDO2 wait for the web front end (open item).

## Context
CEDA §8.3 requires "SSO through OIDC" and "MFA enforced by role and risk: passkeys/FIDO2, TOTP and
recovery codes", and does not accept SMS as a strong factor. Until now only the development login
existed (ADR-007), which is switched off outside dev/test. Staff in Abia State already have work
accounts (Microsoft 365 or Google Workspace), and the state may adopt a government identity service.

## Decision
**Single sign-on through OpenID Connect.**
* **The flow:** authorization code with PKCE (S256), as OAuth 2.1 requires. A `state` value
  (stored only as a hash) and a `nonce` tie the provider's answer to the browser that asked. Each
  attempt lives 10 minutes and completes once. The PKCE verifier is field-encrypted at rest.
* **The ID token is checked in full:** the signature against the provider's published keys
  (RS256, PS256 or ES256 only; never `none` or HMAC), issuer, audience (and `azp`), expiry, issue
  time and nonce. A new signing key at the provider triggers one refetch of its keys.
* **Discovery must agree:** the discovery document must name the configured issuer, and every
  endpoint must be HTTPS.
* **Providers are configuration, per organisation.** The setting `auth.identity_providers` holds
  key, name, issuer, client id, and the options below. Like all configuration it is inherited: Abia
  State sets its Microsoft 365 once and every school beneath the state can use it. A school may set
  its own instead.
* **Client secrets never go in the database.** A provider names a `client_secret_ref`, and the
  secret comes from `SYNTEGRA_OIDC_CLIENT_SECRETS` (from AWS Secrets Manager in production).
* **Set up with** `syntegra configure-sso <tenant> providers.json`, which checks the list and
  prints the redirect URI to register with the provider.

**Signing in never creates an account.** An administrator, or an approved registration (ADR-014),
creates the user. Single sign-on only proves who they are:
* **Normally** the user is found by the provider's subject, stored in `idp_subject` as
  `sha256(issuer)[:16] + ":" + sub`. `sub` is unique only within one issuer, and `idp_subject` is
  unique per organisation, because one teacher may hold accounts at two schools behind the same
  Microsoft 365.
* **First sign-in (only if `link_by_email` is on):** the user may be matched by email, in two cases:
  * the provider is authoritative for the email's domain (`email_domains`, for example the state's
    own Microsoft 365 tenant), or
  * with no domains set, the provider says the email is verified.

  The match is recorded (`IAM.SSO_LINKED`), and from then on the subject decides.
* **Otherwise** the person is told their account is not set up and to ask their administrator.

**MFA, enforced per user (`mfa_required`, on by default).**
* **The factor is an authenticator app (TOTP, RFC 6238):** Google Authenticator, Microsoft
  Authenticator, Authy and others.
* **TOTP parameters:** 160-bit secrets, 30-second steps, 6 digits, and one step of clock drift.
* **No code works twice:** the last accepted step is stored.
* **The secret is field-encrypted:** AES-256-GCM with AAD bound to the row, as with national IDs
  (ADR-008).
* **Ten single-use recovery codes** are shown once at enrolment (stored as SHA-256). A user who has
  passed MFA can replace them.
* **The first factor yields a 5-minute MFA challenge token** (`typ mfa+jwt`, its own audience), not
  an access token. No API accepts it, and it completes once.
* **First sign-in enrols:** the user scans the QR code (`otpauth://` URI) and proves it with one
  code. Sign-in then completes and the recovery codes are returned.
* **Five wrong codes lock the second factor for 15 minutes.** Failures are committed before the 401.
* **Lost phone:** `syntegra reset-mfa` (audited, with a reason) removes the app and the recovery
  codes. The user enrols again at their next sign-in.
* **A trusted provider's own MFA:** with `trust_idp_mfa`, a provider that reports strong MFA in
  `amr` (for example Entra ID's `mfa`) is accepted without asking again. A provider reporting `sms`
  or `tel` is never trusted for MFA (§8.3).
* **Users not required to use MFA** (`mfa_required` off; for example a kiosk account) get tokens
  straight after single sign-on.

**One issuance path.** Every route ends in the same `_open_session` as the dev login (ADR-007). The
access token's `amr` records how the person signed in:

| `amr` | How the person signed in |
|-------|--------------------------|
| `fed` | single sign-on only |
| `fed otp mfa` | single sign-on, then the authenticator app |
| `fed rec mfa` | single sign-on, then a recovery code |
| `fed mfa` | single sign-on, with MFA trusted from the provider |
| `dev` | development login |

The dev login stays exempt from MFA, and exists only in dev/test.

**Endpoints.** All are exempt from Idempotency-Key, like the other sign-in endpoints, and all
answer `Cache-Control: no-store`.

| Endpoint | What it does |
|----------|--------------|
| `GET /v1/auth/providers?tenant=` | the sign-in options to show |
| `GET /v1/auth/oidc/{idp}/start?tenant=[&login_hint=]` | 302 redirect to the provider |
| `GET /v1/auth/oidc/callback` | tokens, or an MFA challenge |
| `POST /v1/auth/mfa/verify` | finish sign-in with a code |
| `POST /v1/auth/mfa/enrolment` | start setting up the authenticator app |
| `POST /v1/auth/mfa/enrolment/confirm` | confirm the app; returns tokens and recovery codes |
| `POST /v1/auth/mfa/recovery-codes` | replace your recovery codes |

## Consequences
* **Tests run the whole exchange** through the API against a fake OpenID Provider that enforces
  PKCE, client authentication and the redirect URI. Covered: replayed callbacks, tampered ID tokens
  (nonce, issuer, audience, expiry), injected codes, expired attempts, wrong client secrets, account
  linking rules, lockout, recovery codes, the provider's MFA and SMS refusal, and the RFC 6238 test
  vectors.
* **The callback returns JSON** for now. When the web front end exists, it will finish the exchange
  and keep the tokens out of the browser address bar.
* **Open:**
  * **Passkeys/FIDO2 (WebAuthn)** need the browser, so they come with the web front end. The
    `mfa_credential.kind` column is ready for them.
  * **Risk-based step-up** (§8.3: new device or country, sensitive actions such as revealing a
    national ID) will use `amr` and session age. Today every MFA-required sign-in asks.
  * **A per-role MFA policy** (for example, MFA required for staff and optional for parents) will
    set `mfa_required` from the role catalogue. Today it is per user, on by default.
