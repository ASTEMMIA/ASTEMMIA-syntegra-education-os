# ADR-003: Audit chain mechanics (§9.1)

**Status:** Proposed

## Decision
* `event_hash = SHA-256(prev_hash ‖ canonical_json(event))`. Canonical JSON uses sorted keys, no
  whitespace and UTF-8. The fields hashed are listed in `core.audit.HASH_FIELDS`.
* One chain per tenant per day. The first event of a day chains from
  `genesis = SHA-256("CEDA-AUDIT-GENESIS|<tenant>|<date>")`.
* `ops.audit_chain_head(tenant_id, chain_date)` is the serialisation point. On MySQL it is taken with
  `INSERT … ON DUPLICATE KEY UPDATE` followed by `SELECT … FOR UPDATE`. That takes an exclusive lock
  whether or not the row existed, so concurrent first writers of a day cannot deadlock. Two rejected
  alternatives:
  * `INSERT IGNORE`: deadlocked under load, from an S-to-X lock upgrade.
  * An autonomous transaction on a second connection: self-deadlocked on gap locks, and starved the pool.
* Writes are audited automatically in `before_flush` for every `Audited` entity, in the same
  transaction. Before and after states are stored only as content hashes, so no personal data is
  duplicated into the audit log.
* `Evidential` rows (including `audit_event`) are refused on ORM update or delete (P7).
* Built-in roles (no tenant) are audited on the nil-UUID platform chain.

## Open
Closing a day's head and anchoring its hash in S3 Object Lock (WORM) is a scheduled job in the
infrastructure track (ADR-005).
