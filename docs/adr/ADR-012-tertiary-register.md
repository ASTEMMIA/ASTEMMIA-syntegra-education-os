# ADR-012: One institution register, kindergarten to university

**Status:** Accepted 2026-11-21

## Context
The platform is the one-stop repository for all education in Abia State (ADR-010). Higher and tertiary
education is overseen directly by the **Abia State Ministry of Tertiary Education**. The school register
(ADR-009) assumed every institution sits under an LGEA and offers school levels only.

## Decision
**One register for every institution.** Each is described by four things:

| | School (ASUBEB) | Tertiary institution (Ministry) |
|---|---|---|
| **Parent:** where it sits in the oversight hierarchy | its LGEA | the Ministry itself, since tertiary institutions are not organised by LGEA |
| **Location:** the local government area it is in | taken from its LGEA | checked against the state's areas (`location.areas`, from the structure file) |
| **Type** | School (may be left blank) | University, Polytechnic, College of education, Technical college, College of health, Other tertiary |
| **Levels** | ECCDE, Primary, JSS, SSS | TVET (NTC/NBC), NCE, ND, HND, Degree, Postgraduate (§2, §23) |

**Rules:**
* **Each body registers only its levels** (`register.levels`, set from the structure file):
  * ASUBEB: ECCDE to SSS
  * the Ministry: TVET to Postgraduate
  * a row for another body's level is rejected with its reason
* **Type and levels must agree:**
  * a school cannot offer tertiary levels
  * a tertiary type must offer at least one tertiary level
  * a tertiary row must state its type
* **Spellings found in real lists are understood:** "ND/HND", "OND", "B.Sc", "PGD", "NTC/NBC", "Poly",
  "COE", "School of Nursing"…
* **Duplicates** are found by government code, or by the same name under the same parent in the same
  location.
* **Imports must be made from a body, not the state root.** An import belongs to one body.
* **Managing body:** the body the user is working in (ASUBEB, Ministry) is recorded on each institution.
  Before this it was the tenant name.

**Schema (migration 0005):**
* `institution.lga_scope_id` is renamed `parent_scope_id`. Existing schools' `location` is filled in
  from their LGEA name.
* Added `location`, `location_key` and `institution_type`.
* `institution_level` accepts the tertiary values.
* Tested on MySQL: upgrade, `alembic check` and downgrade round trip, with a pre-existing school.

**CLI:** `import-institutions FILE --board-code <BODY> [--commit]`. `import-schools` is kept as an alias.

## Next
* **Tertiary academic structure**, inside each institution: faculties, departments, programmes, credit
  records and matriculation (§23.6), Phase 5 in the roadmap.
* **Accreditation references** (NUC, NBTE, NCCE) as identifiers on the institution.
