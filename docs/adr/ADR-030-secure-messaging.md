# ADR-030: Secure messaging between education authorities, schools and parents

**Status:** Accepted 2026-11-22. Built (package `syntegra.messaging`, migration 0019, the Messages
screens, parent sign-in by email code). Open: an SMS sign-in provider, malware scanning of
attachments, push notifications, end-to-end encryption for adult staff-only channels.

## Context
The user asked for messaging "similar to WhatsApp or Zenzap" so that education authorities,
schools and parents can communicate easily; every communication encrypted; documents sent and
received; and access for every teacher, administrator and parent.

CEDA §26 already sets the rules for communication on the platform:

* conversations between adults and children, and with parents, are **institutional records**:
  kept, and reviewable by an authorised officer, so they cannot be end-to-end encrypted in a way
  that locks the state out;
* pastoral and HR matters need a **protected** class with stricter access;
* a parent communicates only about the learners they hold parental responsibility for, and never
  where a court order withholds contact.

Consumer apps encrypt end to end, so the provider cannot read messages and neither can the
school. That fits private chat between adults. It does not fit a state system where a complaint,
a safeguarding concern or a freedom-of-information request must be answered from the record.

## Decision

### Encryption
* **In transit:** TLS everywhere (as the rest of the platform).
* **At rest:** every message body, earlier edit and attachment name is encrypted with AES-256-GCM
  under a per-tenant data key wrapped by the key-encryption key (envelope encryption, the same
  `FieldCipher` as Restricted fields, ADR-008). The associated data binds each ciphertext to its
  tenant, table, column and row, so a ciphertext copied to another row does not decrypt.
  Attachments are stored in the document store (S3 with SSE-KMS in production, ADR-027), with a
  SHA-256 checked on every download.
* **Protected conversations** (pastoral, HR; staff only) use a separate key,
  `SYNTEGRA_MESSAGE_PROTECTED_KEK_B64`, so reading them needs that key as well. The app refuses to
  start outside dev/test without it.
* **Not end-to-end.** The server can decrypt, because the institution must be able to review
  (CEDA §26.2). Review is controlled and logged instead (below). End-to-end encryption remains
  possible later for adult staff-only channels.
* Message text never enters the idempotency store: a retried send gets back only ids.

### Who may message whom
Decided from the platform's own data, not an open directory:

* staff with staff **up and down their own line**: a school with its LGEA, ASUBEB and the
  ministry, and back; not sideways to another school;
* **parents with the staff of their children's schools**, and those staff (and the offices above
  them) with the parents;
* **never parent to parent**; parents do not create groups.

### Conversations
| Kind | Members | Posting |
|---|---|---|
| Direct | two people | both |
| Group | made by staff, with people they may contact; up to 1,024 | all, or administrators only |
| Class | class teacher and assistant (owners), the head (admin), parents of the class's learners | all |
| Staff room | every member of staff at the institution | all |
| Authority channel | an office's staff and the heads and administrators of every institution beneath it | all |
| Announcement | all staff or all parents of an institution or an office's institutions | owners only (`message:broadcast`) |

Class, staff and authority members are **derived** and kept in step on opening: a pupil who
leaves takes their parents out of the class conversation; people added by hand stay.

### Messages
Text up to 4,000 characters; up to five documents per message, 20 MB each (PDF, Word, Excel,
PowerPoint, text, CSV, JPEG, PNG, WebP), each checked by its first bytes, not its name. Replies,
reactions (a fixed set), read receipts (✓ sent, ✓✓ read), edit within 24 hours (the earlier text
is kept, evidential), delete for everyone within 24 hours or by an administrator (hidden from the
conversation, kept as the record). Nothing is ever deleted from the database.

### Moderation and review
* Anyone may **report** a message; reports go to a queue for the conversation's scope.
  `message:moderate` (HEAD, IADM, DSL) dismisses a report or removes the message.
* An officer may **review** a conversation they are not in, deleted messages and earlier edits
  included, only by stating a reason. Each opening and each document download is written to
  `conversation_access` (evidential) and listed to auditors. `message:oversee` (HEAD, DSL, DPO,
  STA) for institutional conversations; `message:oversee_protected` (DSL, DPO) for protected.

### Parents
* A registrar gives a parent an account from Learner 360: only a guardian with parental
  responsibility, not withheld by a court order, of an enrolled learner. The account has the
  Parent role at the child's school.
* Parents sign in with their email address and a **6-digit code** sent to it: valid 10 minutes,
  once, five wrong tries, five codes an hour; the same answer whether or not the address has an
  account. The code is in the email body only (the body is wiped once sent; subjects are kept).
  Only accounts whose every role is Parent can sign in this way; staff use SSO with MFA.
* SMS codes need a provider decision (cost, sender ID registration with the NCC).

### Real time
The app polls: the open conversation every 3 seconds, the list every 5, the unread count every
20, and nothing while the tab is hidden. Enough for the pilot; WebSockets through Redis (or push
notifications) can replace it without changing the API.

### Storage
Schema `msg`: `conversation`, `conversation_member`, `message`, `message_edit`,
`message_attachment`, `message_reaction`, `message_report`, `conversation_access`; plus
`iam.email_sign_in_code`. Conversations cross scopes (a parent, a teacher and an ASUBEB officer
can share one), so these tables are not scope-filtered: every query filters by tenant and by
membership explicitly. Counters and read markers are left out of the audit chain.

### Screens (ADR-028)
Messages for everyone (with the unread count in the bar): conversation list and open
conversation side by side, one at a time on a phone; new chat, group, staff room, heads-of-schools
channel, announcements. "Message the parents" on a class. "Give a parent account" on Learner 360.
"I am a parent or guardian" on the sign-in page. Reports for moderators. Parents see Messages only.

## Consequences
* Every teacher, administrator, officer and parent has messaging; who may reach whom follows the
  structure, so nobody needs to maintain a contact list.
* The state can answer for what was said: messages are kept, and officers can read them, but
  only for a recorded reason.
* Because messages are not end-to-end encrypted, the key-encryption keys must be guarded as
  carefully as the Restricted-field keys (KMS, rotation, separate protected key).
* Attachments are not yet scanned for malware: `scan_status` is recorded as `not_scanned` until
  a scanner (e.g. ClamAV on upload, or Amazon GuardDuty Malware Protection for S3) is chosen.
* Polling costs a request every few seconds per open tab; at the pilot's scale this is small.
