# ADR-018: Processing SES bounces, complaints and deliveries

**Status:** Accepted 2026-11-22.

## Context
ADR-017 sends email through Amazon SES but does not hear back. A proprietor who types their email address
wrongly never receives the verification code. The regulator cannot see why the application stalls, and the
applicant was blocked from applying again for seven days. Mailbox providers also judge senders by how
they handle bounces and complaints, and SES pauses accounts whose bounce or complaint rate stays high.

## Decision
**Pull, do not push.** The SES configuration set publishes Bounce, Complaint, Delivery, DeliveryDelay and
Reject events to an SNS topic. An SQS queue subscribes to that topic and has a dead-letter queue. The worker
`syntegra email-events` long-polls the queue.
* **No public endpoint and no SNS signature handling.** The queue policy allows only the topic to write,
  and only the worker's role may read.
* **Envelopes:** events are accepted SNS-wrapped, raw, or from EventBridge.

**Applying an event:**
* **Matched to the message we sent** by the SES `MessageId`, which is stored on `ops.outbound_email`
  (unique since migration 0011). Events for messages or recipients this system did not send are ignored.
  The account may have other senders.
* **Recorded once** in `ops.email_event`. Delivery is at least once, so events are de-duplicated by a hash
  of message, recipient, kind and SES feedback id (or the event time).
* **Order does not matter:** `delivery_status` only moves to a more decisive state:

  sent < delayed < soft_bounced < delivered < rejected/suppressed < bounced < complained

  A late "delayed" never hides a bounce.
* **A message that cannot be parsed or applied is not deleted.** SQS redelivers it, then moves it to the
  dead-letter queue for a person to look at.

**Suppression (`ops.email_suppression`):**
* **What suppresses an address:** a permanent bounce or a complaint. A transient (soft) bounce does not.
* **Only a SHA-256 of the normalised address is stored:** enough to check an address, not to read it back.
* **The dispatcher checks it before every send.** A suppressed message is never handed to SES: it is
  marked `suppressed` and its body (it may hold a code) is wiped. This sits alongside the SES account-level
  suppression list, and makes the outcome visible in our own data.
* **Lifting it:** `syntegra email-unsuppress --address …` allows an address again, for example when a
  school's mailbox is fixed. Complaints should be lifted only at the person's request.

**Private-school registration (ADR-014, ADR-015):**
* **Email is linked to its application:** verification email carries the application reference
  (`outbound_email.ref`).
* **The regulator sees the outcome:** the review queue and application views show `email_delivery`
  (sent, delivered, bounced…).
* **The proprietor is told:** checking the status of an application whose email bounced says so, and
  asks them to apply again with an address that receives mail.
* **It no longer blocks:** an unverified application whose email bounced, was rejected or was suppressed
  stops blocking a new application for the same school at once, instead of after seven days.
* **Suppressed addresses are refused up front:** applying, or asking for a new code, with a suppressed
  address is refused. The message says what to do, not whether it bounced or someone complained.

**Migration 0011:**
* new tables `email_event` and `email_suppression`
* on `outbound_email`: the new columns `ref` and `delivery_status`, and `provider_message_id` made unique

## Setting up (with ADR-017)
1. Create an SNS topic, an SQS queue with a dead-letter queue (`maxReceiveCount` 5), and the subscription
   between them. In the queue policy, allow `sns.amazonaws.com` with `aws:SourceArn` set to the topic.
2. Add an SNS event destination to the configuration set for Bounce, Complaint, Delivery, DeliveryDelay
   and Reject. Then set `SYNTEGRA_SES_CONFIGURATION_SET`.
3. Give the worker's role `sqs:ReceiveMessage` and `sqs:DeleteMessage` on the queue. Set
   `SYNTEGRA_SES_EVENTS_QUEUE_URL`, and run `syntegra email-events` as a worker service.
4. Alarm on the dead-letter queue's depth, and on the SES account's bounce rate (above 2%) and
   complaint rate (above 0.05%).

## Consequences
* **Tests cover:**
  * every envelope, and duplicates and out-of-order events
  * suppression and lifting it
  * the worker against botocore's Stubber on a real SQS client (applied messages deleted, broken ones
    left for the dead-letter queue)
  * the whole registration story: bounce, the regulator's view, the proprietor's status, refused
    re-send, and re-applying with a working address
* **Open:** letting the regulator correct a proprietor's email on an application, rather than the
  proprietor re-applying.
