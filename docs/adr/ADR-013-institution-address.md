# ADR-013: Full postal address and postcode for every institution

**Status:** Accepted 2026-11-21

## Context
The programme owner asked for the full address and postcode of every primary school, secondary school
and tertiary institution. Address formats and postcodes differ by country, and what a jurisdiction
insists on is a local decision (P4).

## Decision
* **Every institution records:**
  * `address_line1` (street or plot)
  * `address_line2` (optional)
  * `town`
  * `postcode`
  * its **LGA** (`location`, ADR-012)

  The **state** and **country** come from the structure file. Migration 0006 renames the old free-text
  `address` to `address_line1`, so existing values are kept, and indexes `postcode`.
* **A composed one-line address** is returned with every institution, for example
  `2 Polytechnic Road, PMB 7166, Aba, Aba South LGA, Abia State 450001, Nigeria`. The LGA is left out
  when it repeats the town.
* **The postcode format and required parts are data**, in the structure file:
  * the country's `address.postcode_pattern`, label and example
  * the jurisdiction's `address_required`

  Abia: Nigeria's 6-digit NIPOST postcode; **address line 1, town and postcode are required**, as
  directed. A row that misses one, or has a malformed postcode, is rejected with the reason and an
  example.
* **Postcodes are normalised:**
  * upper-case and single spaces
  * a code typed with a space ("440 001") is accepted when it matches without the space
  * formats that contain a space, such as the UK's "SW1A 1AA", match as typed
* **Spreadsheet headers seen in practice are understood:** "Address", "Street", "City", "Postal Code",
  "Zip", "Local Government Area".
* **Tests:** Abia's rules, the composed address, header variants, and a UK structure with its own
  postcode format and no code changes.

## Note
If some institutions do not yet have a postcode, the requirement can be relaxed for the state by removing
`postcode` from `address_required` in `ng-abia.json` and re-running `seed-structure`. No code change is
needed.
