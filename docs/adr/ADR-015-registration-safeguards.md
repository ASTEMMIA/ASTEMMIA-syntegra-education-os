# ADR-015: CAPTCHA and email verification for public forms

**Status:** Accepted 2026-11-22. The Amazon SES sender is built (ADR-017).

## Context
The private school application (ADR-014) is public: anyone can submit it without an account. ADR-014
required a human-verification challenge and verification of the proprietor's email before production.

## Decision
**CAPTCHA, checked before any database work.**
* **Where:** applying and requesting a new verification email both need a passed CAPTCHA token
  (`captcha_token`).
* **Production:** `captcha_provider=siteverify`, configured by `SYNTEGRA_CAPTCHA_VERIFY_URL` and
  `SYNTEGRA_CAPTCHA_SECRET`. This works with **Cloudflare Turnstile, hCaptcha or Google reCAPTCHA**:
  each takes `secret` + `response` (+ `remoteip`) and answers `{"success": …}`.
* **It fails closed:** if the provider cannot be reached, the form is not accepted.
* **The site key** for the web form is served by `GET /v1/public/{jurisdiction}/private-school-applications/form`,
  together with the jurisdiction's required address fields and postcode example.
* **Dev/test** use a stand-in that accepts only `dev-ok`. Staging and production **refuse to start**
  without a real provider.

**Email verification, before review.**
* **The code:** submitting emails the proprietor a link with a one-time code. The code is 192 bits,
  valid for **48 hours**, stored only as a SHA-256 hash, compared in constant time, and cleared once
  used.
* **Status page:** shows `awaiting_email_verification` until it is used.
* **The review queue** hides unverified applications by default (`include_unverified=true` shows them).
  The regulator **cannot start review** of an unverified application, but **can reject** one, so
  obvious spam can be closed.
* **Resending** (`…/resend-verification`) needs the reference, the proprietor's email and a CAPTCHA,
  has a 60-second gap, and allows at most 5 sends. A new code replaces the old one.
* **Expiry:** an application never verified stops blocking a new application for the same school after
  **7 days**.

**Reliable delivery.**
* **Queued with the application:** email is written to `ops.outbound_email` in the **same transaction**
  as the application, so neither exists without the other.
* **Delivered afterwards:** a dispatcher (`python -m syntegra.cli send-email`) sends it, retries
  failures, and **wipes the body once sent** so the code is not kept.
* **Dev/test** print and log messages. Staging and production **refuse to start** without a real sender.

## Open
* ~~**The Amazon SES sender adapter.**~~ Built in ADR-017.
* **WAF rate limits** on `/v1/public/*` still belong in front of the application (infrastructure track).
* **The web form itself:** CAPTCHA widget, verify page at `{public_base_url}/private-schools/verify`.

## Schema
Migration 0008:
* new table `ops.outbound_email`
* new columns on `registration_application`: `email_verified_at`, `email_token_hash`,
  `email_token_expires_at`, `verification_sends` and `last_verification_sent_at`

Round trip tested on MySQL.
