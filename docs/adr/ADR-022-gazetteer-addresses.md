# ADR-022: Gazetteer: one address register for people and institutions

**Status:** Accepted 2026-11-22. Built from the "Gazetteer Service" data model supplied (ZETTER ADDRESS FOR
EDUCATION, Visio).

## Context
Addresses existed only as columns on an institution (ADR-013). People (learners, guardians, staff) had
none. The supplied model defines four entities:
* **Address:** address number, person or organisation.
* **Address Type:** ID and description.
* **Party:** person or organisation.
* **Person_Address:** first, middle and surname; medicare number; date of birth; address type; address
  lines 1 to 5; state; local government area; zipcode; PAF key; address description; effective from and to.

## Decision
**Three tables in the `mdm` schema (migration 0014):**

| Model entity | Table | Notes |
|--------------|-------|-------|
| Address | `mdm.address` | `address_no` (per organisation), `address_line1`-`address_line5`, `local_government_area`, `state`, `country`, `postcode` (the model's *zipcode*), `paf_key`, `address_description` |
| Address Type | `mdm.address_type` | code and description: HOME, POSTAL, CORRESPONDENCE, TEMPORARY (person); PREMISES, CAMPUS (organisation); BILLING |
| Party (+ address link) | `mdm.party_address` | a person **or** an institution, with an address, a type, `effective_from` and `effective_to` |
| Person_Address | a view: `GET /v1/person-addresses` | names, date of birth and addresses, joined; filter by LGA, postcode, type |

**Rules:**
* **One physical place is one address within a scope, shared.** Siblings and guardians link to the
  same home. Case, accents and punctuation are ignored when matching ("14 FAULKS ROAD." = "14 Faulks
  Road").
* **Checked against the structure file (ADR-011, ADR-013):**
  * the local government area must be one of the state's ("Aba North LGA" is understood);
  * the postcode must be the 6-digit NIPOST format ("450 001" is accepted);
  * state and country are filled in;
  * the PAF key is upper-cased and checked for stray characters.
* **One current address of each type per party.** Setting a new one ends the old one the day before;
  the same address again changes nothing; ended addresses are kept as history.
* **Type fits the party:** HOME is for people, PREMISES is for organisations.
* **Institutions get a PREMISES address on registration,** from their register address: line 1,
  line 2, town as line 3, LGA and postcode. Approved private schools get one too. Institutions
  registered before this change get one from `syntegra backfill-addresses`.
* **Privacy:** an address and its links live in the party's scope, so a learner's home address is
  visible where the learner is and nowhere else. Reading needs `person:view`; setting a person's
  address needs `person:edit` (registrar, institution administrator); institutions need
  `institution:edit`.
* **Events:** `PER.ADDRESS_LINKED` and `PER.ADDRESS_ENDED` for people, `INS.ADDRESS_LINKED` and
  `INS.ADDRESS_ENDED` for institutions. Payloads carry ids only. Changes are audited on the tenant's
  chain.

**API:**

| Endpoint | What it does |
|----------|--------------|
| `GET /v1/address-types` | the address types |
| `GET`, `POST /v1/persons/{id}/addresses` | a person's addresses (`?history=true` includes ended ones); set one |
| `GET`, `POST /v1/institutions/{id}/addresses` | the same for an institution |
| `POST /v1/party-addresses/{id}/end` | end an address, keeping it as history |
| `GET /v1/addresses/{id}` | one address |
| `GET /v1/person-addresses` | the Person_Address view |

## Deliberately not included
* **`medicare_number`:** a health-insurance identifier from another country's model. A Nigerian
  equivalent (NHIA) would be special-category health data (CEDA §8). If it is wanted, it belongs as an
  external identifier on Person 360, encrypted with a blind index like the NIN, and needs a
  data-protection decision first.
* **Names and date of birth are not copied into the address tables.** They stay on Person 360 and are
  joined in the Person_Address view, so a correction to a name shows everywhere.

## Consequences
* The institution's own address columns stay as the register's summary (ADR-013) and seed its PREMISES
  address.
* **Open:**
  * importing addresses for learners and guardians in bulk, with the learner module;
  * using a shared home address as a duplicate-matching signal (ADR-008);
  * adding NIPOST gazetteer lookups when a feed is available.
