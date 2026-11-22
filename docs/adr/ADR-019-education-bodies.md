# ADR-019: Education bodies and institutions' links to them

**Status:** Accepted 2026-11-22. The result-exchange adapters are open items.

## Context
Institutions answer to bodies outside the state's own structure. The platform needs:
* **centre numbers** to plan examinations: WAEC, NECO, NABTEB, NBAIS, or international boards
  (Cambridge, Pearson Edexcel, IB, College Board);
* **JAMB institution codes** to follow admissions;
* **accreditation** from the regulators: NUC (universities), NBTE (polytechnics and technical
  colleges), NCCE (colleges of education), NMCN and CHPRBN (health training);
* **funders:** UBEC (basic education) and TETFund (tertiary).

The CEDA plans for these as **awarding bodies** (§4 Qualification 360), **Institution 360
accreditation**, result-exchange **adapters** (§7.3) and **TRCN teacher registration** (§27). Like
the state structure (ADR-011), which bodies exist differs by country.

## Decision
**Two kinds of body, one listing:**
* **Internal bodies** are the jurisdiction's own: ASUBEB and the Ministry of Tertiary Education in
  Abia. They are the structure (ADR-010, ADR-011), and `GET /v1/education-bodies` lists them from it.
* **External bodies** come from **catalogue files**, as data:
  * `structures/bodies/ng.json`: WAEC, NECO, NABTEB, NBAIS, JAMB, NUC, NBTE, NCCE, NMCN, CHPRBN,
    UBEC, TETFund, TRCN, NERDC.
  * `structures/bodies/international.json`: Cambridge, Pearson Edexcel, IB, College Board.
  * A structure file names the catalogues that apply (`"education_bodies": ["ng", "international"]`)
    and `seed-structure` loads them. `seed-bodies` re-applies them after an edit. Bodies are never
    deleted, only updated.
* **Each body records** its code, name, scope (international, regional, national or state),
  countries, roles, what it covers (institution types and levels), and its examinations with the
  levels each is for.
* **The register is stored in `org.education_body`:** platform reference data shared by every tenant,
  audited on the platform chain like the built-in roles.

**Links (`org.institution_body_link`).** An institution's standing with a body is a link. What a body
does decides which links it can have:

| Role | Link | Needs |
|------|------|-------|
| examination | `exam_centre` | the examination (e.g. `WASSCE-SC`) and the centre number |
| admissions | `admissions_code` | the body's code for the institution (e.g. its JAMB code) |
| regulator | `accreditation`, `approval` | optional reference; status and dates |
| funding | `beneficiary` | — |
| curriculum, professional registration (NERDC, TRCN) | none | they relate to curricula and staff |

**Checks:**
* **The body must cover the institution:** its type, or one of its levels. NUC accredits universities,
  not polytechnics. JAMB codes are for tertiary institutions, not schools.
* **An exam centre's examination must be one the body runs, for a level the institution offers.** A
  primary school cannot be a WASSCE centre; NCEE is for primary schools.
* **One number, one institution:** a body's number for an examination belongs to one institution on
  the whole platform. The check spans tenants but does not reveal which institution holds the number.
* **One current link at a time:** an institution has one current link per body, kind and examination.
  It is ended (`suspended` or `withdrawn`, with a date and note) before it is replaced, and ended links
  are kept as history.
* **Links follow scope:** they live in the institution's tenant and scope, so ASUBEB sees its schools'
  WAEC centres, the Ministry does not, and the state sees both.
* **Status values:** `active`, `provisional` (for example interim accreditation), `suspended`,
  `withdrawn`. Optional `valid_from` and `valid_to`; a link past its `valid_to` is not current.
* **Events:** `INS.BODY_LINKED` and `INS.BODY_LINK_ENDED`.

**Bulk.** Bodies hand over lists, such as a WAEC centre list. A CSV is loaded in two steps: check, then
record. Every problem row comes back with its reason, and duplicates within the file are caught.
* **API:** `POST /v1/bulk/institution-body-links[?commit=true]`
* **CLI:** `import-body-links FILE --board-code ASUBEB [--commit]`
* **Columns:** `institution` (INS code or the government code), `body`, `link_type`, `exam`,
  `reference`, `status`, `valid_from`, `valid_to`, `note`.

**Candidates.** NECO and NABTEB candidate numbers join the existing WAEC and JAMB numbers as person
identifiers (ADR-008), encrypted with a blind index. Their formats are only loosely checked (letters
and digits) until the bodies confirm them.

## Consequences
* A state or country with other bodies adds a catalogue file; no code changes.
* **Open:**
  * **Result exchange adapters** (CEDA §7.3) for WAEC, NECO, NABTEB and JAMB: verifying results and
    receiving them. These need data-sharing agreements with each body; they offer result checkers to
    the public, not open APIs. They also need the Phase 1 learner and assessment modules.
  * **TRCN registration** on staff records (§27), with the staff module.
  * **NERDC curricula** as Curriculum 360 (§24).
  * **Exam series** (for example WASSCE May/June 2027) and candidate entries, with the assessment module.
