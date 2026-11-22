# ADR-017: Email delivery through Amazon SES

**Status:** Accepted 2026-11-22. Bounce and complaint processing: ADR-018.

## Context
ADR-015 queues email in the same transaction as the change that needs it (`ops.outbound_email`), and a
dispatcher delivers it afterwards. Only a development sender existed, so staging and production could
not start. The first real use is the proprietor's verification email for private-school registration
(ADR-014).

## Decision
**Amazon SES, API v2 `SendEmail`, through the AWS SDK (boto3).**
* **Region:** the residency region by default (`af-south-1`, Cape Town, where SES is available), so
  email content stays in-region. `SYNTEGRA_SES_REGION` can override it.
* **Credentials:** the standard AWS chain, so the ECS task role in production. No access keys go in
  settings or the environment.
* **Retries:** boto3 retries network and 5xx errors itself ("standard" mode, 3 attempts, 5 s connect
  and 10 s read timeouts). Calls run in a worker thread, because boto3 is blocking.
* **The message:** plain text, UTF-8. A `purpose` tag (for example `private-school-verification`)
  splits SES metrics by kind of email.
* **Configuration set:** set `SYNTEGRA_SES_CONFIGURATION_SET` for reputation metrics and event
  publishing.

**The dispatcher treats failures by kind:**

| SES answer | What happens |
|------------|--------------|
| Success | Marked sent; the SES `MessageId` is stored; the body is wiped (it may hold a one-time code) |
| `MessageRejected`, `BadRequestException`, `MailFromDomainNotVerifiedException`, `NotFoundException` | Permanent: not retried; the body is wiped |
| `TooManyRequestsException`, `LimitExceededException`, `SendingPausedException`, `AccountSuspendedException`, throttling | The batch stops at once and no attempt is counted; the next run tries again |
| Anything else | Retried after 1, 2, 4, 8 … minutes (at most a day), up to 8 attempts; the body is wiped when it gives up |

Migration 0010 adds `next_attempt_at` and `provider_message_id` to `ops.outbound_email`.

**Production refuses the development sender** (unchanged from ADR-015). `SYNTEGRA_EMAIL_SENDER=ses`
is required outside dev/test.

## Setting up SES (once per environment)
1. **Verify the sending domain** in SES in the region, with Easy DKIM (three CNAME records).
2. **Set up the domain records:** a custom MAIL FROM subdomain with its SPF record, and a DMARC record
   (`p=quarantine` or stricter once reports are clean).
3. **Request production access.** A new SES account is in the sandbox and can send only to verified
   addresses.
4. **Give the dispatcher's task role** `ses:SendEmail` on the identity (and the configuration set, if
   used). Nothing else.
5. **Set the environment:**
   * `SYNTEGRA_EMAIL_SENDER=ses`
   * `SYNTEGRA_EMAIL_FROM=no-reply@<verified domain>`
   * optionally `SYNTEGRA_SES_CONFIGURATION_SET`
6. **Check it:** run `syntegra email-test --to <your address>`, then run the dispatcher as a worker
   service with `syntegra send-email`.

## Consequences
* **Tests use botocore's Stubber on a real SES v2 client**, so every request is checked against the
  official API model with no network. They cover delivery, permanent rejection, throttling and backoff.
* **The SES account-level suppression list** stops sending to addresses that bounced or complained.
* **Open:**
  * ~~Process bounce and complaint events~~: done in ADR-018.
  * HTML email templates.
