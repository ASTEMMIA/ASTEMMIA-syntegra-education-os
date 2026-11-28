# ADR-020: Adult and non-formal education in the register; NMEC and MDCN

**Status:** Accepted 2026-11-22. In Abia, **Abia-SAME** registers adult learning centres (confirmed).

## Context
CEDA §2 lists **adult and non-formal education** as a sector (ISCED 2-8), and §23.8 describes adult,
CPD and lifelong learning. The institution register (ADR-012) had no place for it: no level, no
institution type. The catalogue of education bodies (ADR-019) also lacked two bodies:
* **NMEC**, the National Commission for Mass Literacy, Adult and Non-Formal Education, which the CEDA
  did not name;
* **MDCN**, the Medical and Dental Council of Nigeria, which §2 names for medical and health programmes.

## Decision
* **A new level, `adult`** ("Adult and non-formal"): basic literacy, post-literacy and continuing
  education. Real-list spellings are understood: "Adult literacy", "Post-literacy", "Mass literacy",
  "Non-formal", "Continuing education".
* **A new institution type, `adult_learning_centre`.** Lists may say "Literacy centre", "Mass literacy
  centre", "Adult education centre" or "Non-formal education centre". A row whose only level is Adult
  defaults to this type.
* **Adult education is its own sector:**
  * an adult learning centre offers the Adult level only;
  * the Adult level is offered only by adult learning centres;
  * it is neither a school level nor a tertiary level.
* **NMEC** is in the Nigeria catalogue as the regulator of adult learning centres (accreditation or
  approval links).
* **MDCN** is in the Nigeria catalogue as the regulator of medical and dental schools, which sit within
  universities.
* **Migration 0013** widens the check constraints on `org.institution.institution_type` and
  `org.institution_level.level`. Its downgrade refuses to run while adult learning centres are
  registered, rather than deleting them.

## Consequences
* **Abia-SAME registers the centres in Abia.** Like every level, the Adult level is registered only by
  a body whose `registers` list in the structure file includes it. The **Abia State Agency for Mass
  Literacy, Adult and Non-Formal Education (Abia-SAME)** provides literacy, adult education and
  non-formal programmes across the state. It is a third body in `ng-abia.json` (code `ABIA-SAME`,
  label "Agency"), registering `adult`. This was a data change, not a code change.
  * It has no sub-units, like the Ministry: its centres sit directly beneath it, and their local
    government area is checked against Abia's 17.
  * Running `seed-structure ng-abia` again adds it to an existing Abia setup.
  * Import a list of centres with `import-institutions centres.csv --board-code ABIA-SAME`.
* Other states add their own agency for mass education to their structure file in the same way.
* **Private registration is unchanged:** ASUBEB regulates private schools only.
* **Not added:** the National Commission for Nomadic Education. It is not in the CEDA and has no
  institutions in Abia's structure. It can be added to the catalogue the same way if needed.
