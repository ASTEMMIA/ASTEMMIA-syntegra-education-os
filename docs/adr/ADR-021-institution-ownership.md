# ADR-021: Ownership in the register: private tertiary institutions and centres

**Status:** Accepted 2026-11-22.

## Context
Every imported institution was recorded as government-owned (ADR-012). The Ministry of Tertiary Education
oversees private universities and colleges as well, and their list mixes both, so private institutions
were either recorded wrongly or left out. Private schools are different: by the state directive they
apply online and ASUBEB approves them (ADR-014), so a list must not register them behind that workflow.

## Decision
**Two optional columns in institution imports, templates and `POST /v1/institutions`:**
* **`ownership`:** Government (state or federal) or Private. "State", "Federal", "Govt", "Public" and
  "Privately owned" are understood. Blank means Government, so existing lists keep their meaning.
* **`proprietor`:** the owner of a private institution, a person or an organisation. Giving it for a
  government institution is an error.

**The state directive still holds.** A private row is refused when its levels are ones whose private
institutions apply online to the body doing the import (setting `private.registration` from the structure
file):
* **ASUBEB:** private ECCDE, primary and secondary schools are refused, with the message "apply online
  themselves".
* **The Ministry and Abia-SAME:** they may list private institutions directly.

**Where ownership shows:**
* **The register:** each institution shows its ownership and proprietor, and
  `GET /v1/institutions?ownership=private|public` filters by it.
* **The import check:** its summary counts rows by ownership.

**Bodies can be limited by ownership.** A catalogue entry's `covers.ownership` restricts which
institutions it may be linked to (ADR-019):
* **TETFund and UBEC** cover government-owned institutions only, so a private university cannot be a
  TETFund beneficiary.
* **Regulators** (NUC, NBTE, NCCE…) cover both.

**No migration.** `org.institution.ownership` and `proprietor_name` already existed.

## Consequences
* **Private tertiary institutions and private adult centres are recorded in the state register under
  their body.** Unlike approved private schools (ADR-014), they do not yet get their own tenant or a
  proprietor account. Onboarding them to run their own operations on the platform is a later step.
* **The Ministry and Abia-SAME templates** have Ownership (a drop-down) and Proprietor columns.
  ASUBEB's template stays government-only.
