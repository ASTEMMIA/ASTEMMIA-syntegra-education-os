# ADR-008: Person master record (Person 360)

**Status:** Accepted 2026-11-21 for Phase 0. Open items are listed at the end.

## Context
CEDA §4 makes Person 360 the single place a human is stored: the supertype of learner, guardian, staff
and user (P1). It requires:
* probabilistic matching at creation and import, with an auto-link threshold that blocks creation and a
  review band that creates a `match_review` task
* reversible merges with source-trust survivorship
* a data-quality index
* national identifiers encrypted at field level and found by HMAC (§6.2, §8.1)

## Decision

**Tables (`mdm` schema, migration 0003):**
* **`person`:** the master row.
* **`person_name`:** previous names, aliases and transliterations, all of them matchable.
* **`person_contact`:** E.164 phone numbers and email addresses.
* **`external_identifier`:** encrypted, with a blind index.
* **`match_review`** and **`person_merge`:** duplicate decisions and reversible merges, with snapshots.

All are tenant-scoped with the §6.1 standard columns and audited on the tenant hash chain.

**Matching:**
* **Explainable by design (P13).** The score is the sum of named reasons a registry officer reads, such as
  `dob_exact +30`, `names_swapped +34` or `identifier_conflict −45`.
* **Normalisation:** names are case-folded and stripped of diacritics, so "Adébáyọ̀ Ọláòyè" matches
  "Adebayo Olaoye".
* **Phonetic keys:** each token gets **Metaphone + NYSIIS** (jellyfish). Double Metaphone was rejected:
  the only Python implementation was last released in 2016 and has no wheels. Two maintained codes plus
  Jaro-Winkler similarity give equivalent recall.
* **What counts as evidence:** swapped given/family names, swapped day/month in the date of birth, a
  shared phone or email, middle names, and recorded previous names.
* **Calibration** (unit-tested):
  * the same child re-registered, names swapped, day and month swapped, or a spelling variant → **review**
  * twins sharing a surname, date of birth and parent's phone → **review**, never auto-blocked
  * namesakes with another date of birth, and siblings → **no match**
  * a shared NIN/UPN/ULN/BVN/passport → **blocked**, and an identical national ID can never be
    registered twice
* **Thresholds are configuration (P4):** `mdm.match.auto_link` (default 85) and `mdm.match.review`
  (default 60), resolved through the scope hierarchy. A steward may override a block only by naming each
  candidate and giving a reason; that is stored as a decided review.
* **Organisation-wide, privacy-preserving (P6):** candidates are looked for across the whole tenant, so
  duplicates across campuses are caught. A candidate outside the caller's scope is reported as "a possible
  match exists elsewhere" with no name, date or ID. Its review is filed at the lowest scope containing
  both records (ADR-009), under a recorded `duplicate-escalation` purpose, where a steward can see both.

**Merge and un-merge:**
* **Survivorship:** per field, a known value beats an empty one, then the higher source trust
  (`mdm.source_trust`, default NIMC 100 > exam bodies 90 > registry 80 > manual 60 > import 50 > self 40),
  then the more recent value.
* **What moves:** identifiers, contacts and names move to the survivor. The merged name is kept as an
  alias, and the merged id resolves to the survivor on read.
* **Conflicting national IDs** (two different NINs) block the merge.
* **Un-merge** restores everything from the snapshot, but keeps survivor fields edited after the merge
  and reports them, so a later correction is never silently undone.

**Restricted data (§8.1):**
* **Encryption:** identifier values use AES-256-GCM envelope encryption. A per-tenant data key is wrapped
  (RFC 3394) by the key-encryption key, and the associated data binds each value to its tenant, table,
  column and row.
* **Lookup:** a regional HMAC-SHA256 blind index serves lookup and uniqueness. It is regional, so a future
  state-wide de-duplication can compare across tenants.
* **Display:** values are shown masked (`•••••••8901`). `POST …/reveal` needs `learner_national_id:reveal`
  plus an `X-Purpose`, and writes a `reveal` event to the audit chain (§9.1 read audit).
* **Keys:** staging and prod refuse to start without `SYNTEGRA_FIELD_KEK_B64` and
  `SYNTEGRA_BLIND_INDEX_KEY_B64`. Dev/test use a fixed, public development key.

**Data quality (§16.3):** each person carries a 0–100 score: completeness 50, validity 30,
uniqueness 10, timeliness 10. It comes with a list of "what to do next" issues and is recomputed on
every write.

**Permissions** (Phase 0 extension to §21):

| Permission | Roles |
|---|---|
| `person:view` | REG, IADM, HEAD, DPO; TCH own learners only (C) |
| `person:create`, `person:edit` | REG, IADM |
| `person:merge` (resolve duplicates, merge, reverse, override a block) | REG, IADM |

**Events:** `PER.CREATED`, `PER.UPDATED` (field names only), `PER.MERGED`, `PER.UNMERGED`,
`PER.MATCH_REVIEW_OPENED`, `PER.MATCH_REVIEW_RESOLVED`. Payloads carry IDs only.

**API:**
* `POST /v1/persons` (409 `possible-duplicate` with explained candidates)
* `POST /v1/persons/match` (a read-only preview, exempt from Idempotency-Key)
* `GET /v1/persons/{id}` (returns an ETag) and `PATCH` (If-Match required: 428 if missing, 412 if stale)
* identifiers, contacts and reveal
* `GET /v1/match-reviews` and decisions
* merge and reverse

## Open
* **Cross-tenant identity:** resolved for government schools by ADR-009 (one ASUBEB tenant, with
  reviews routed to the lowest common scope). A learner moving between a **private** school tenant and
  the state system still needs the transfer workflow (§22) in Phase 1.
* **AWS KMS key provider** in place of `LocalKeyProvider`, before production.
* **More matching evidence:**
  * guardian contact and prior institution, which arrive with Families and Enrolment in Phase 1
  * Ajami (Arabic-script Hausa) transliteration
  * the trained classifier of §12, fed by stewards' decisions
* **`gender_identity`** (Restricted) is not captured yet.
* **NIMC verification adapter**, which sets `verified_by_adapter`.
