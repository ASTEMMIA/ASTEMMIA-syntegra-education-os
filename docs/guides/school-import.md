# Registering schools and tertiary institutions from a spreadsheet

All Abia State government kindergarten (ECCDE), primary and secondary schools are registered under ASUBEB,
one of the boards inside the Abia State organisation
([ADR-010](../adr/ADR-010-abia-state-kindergarten-to-university.md)). Work as an ASUBEB user, with ASUBEB as
your active scope, when you upload. Load them from one spreadsheet saved as **CSV (UTF-8)**.

## Columns

| Column | Required | Example | Notes |
|---|---|---|---|
| `name` | yes | Community Primary School Ndume | 3–200 characters |
| `type` | tertiary only | Polytechnic | School (default), University, Polytechnic, College of education, Technical college, College of health, Other tertiary. "Poly", "COE" and "School of Nursing" are understood. |
| `lga` | yes | Umuahia North | One of Abia's 17 LGAs. "Umuahia North LGEA" and "umuahia north l.g.a." also work. |
| `levels` | yes | Primary · JSS; SSS · ND/HND · Degree; Postgraduate | Schools (ASUBEB): ECCDE, Primary, JSS, SSS. Tertiary (Ministry): TVET, NCE, ND, HND, Degree, Postgraduate. "Pry", "Secondary", "ND/HND", "B.Sc", "PGD" and "NTC/NBC" are understood. |
| `government_code` | no | ASB/UN/001 | ASUBEB's own school number. Strongly recommended: it is the surest way to avoid duplicates. |
| `address_line1` | **yes** in Abia | 12 Market Road | Street or plot. A column headed "Address" or "Street" also works. |
| `address_line2` | no | Off Amaekpu Junction | Landmark, PMB, area. |
| `town` | **yes** in Abia | Ohafia | A column headed "City" also works. |
| `postcode` | **yes** in Abia | 440001 | The 6-digit NIPOST postcode. "440 001" is accepted. A column headed "Postal Code" also works. |
| `phone`, `email` | no | 0803 123 4567 | Phone numbers are stored in international form (+234…). |
| `latitude`, `longitude` | no | 5.5320, 7.4860 | Both or neither. |

**Templates for the official lists.** Send these to the bodies that hold the lists:

| Template | For |
|---|---|
| [`ASUBEB-schools-template.xlsx`](templates/ASUBEB-schools-template.xlsx) | ASUBEB: every government ECCDE, primary and secondary school |
| [`Ministry-tertiary-institutions-template.xlsx`](templates/Ministry-tertiary-institutions-template.xlsx) | Ministry of Tertiary Education: government-owned post-secondary institutions |

Each template has drop-down lists (the 17 LGAs, institution types and level combinations), postcode and
phone columns kept as text, and a *Read me* sheet with an example row. Officials can return the Excel
file, or save its data sheet as CSV UTF-8. Either way, nothing is registered until the check passes.
The system also serves a bare CSV header (`GET /v1/bulk/institutions/template`).

Imported rows are recorded as **government-owned**. Private schools register themselves (below). Private
tertiary institutions are not imported this way yet.

## Tertiary institutions

Universities, polytechnics, colleges of education, technical colleges and colleges of health are
registered under the **Abia State Ministry of Tertiary Education**, using the same file format. Work with
the Ministry as your active scope.
* `type` is required.
* `lga` is where the institution is located, checked against Abia's 17 LGAs.
* Each body registers only its own levels: ASUBEB rejects a polytechnic row, and the Ministry rejects a
  primary-school row, with the reason given.

Every registered institution gets a **full one-line address**, for example
*12 Market Road, Off Amaekpu Junction, Ohafia, Abia State 440001, Nigeria*.

## How the import works

1. **Upload:** nothing is registered at this point.
2. **Check:** every row is validated and you get a report:
   * how many rows are valid, by LGA and by level
   * each problem row with its row number and what to fix, for example
     *"row 14: LGA 'Umuahia Nort' is not one of this board's LGEAs"* or
     *"row 37: 'Central School Aba' also appears on row 12 for Aba South"*
3. **Fix and re-upload** the problem rows, or go ahead with the valid ones.
4. **Register:** each valid school gets:
   * a system ID (`INS-NG-000001` …)
   * its place under its LGEA, so heads, registrars and teachers can be given access to it
   * an `INS.REGISTERED` event
5. **Report:** what was registered and what was skipped. A school registered by someone else since the
   check is skipped, never duplicated.

**Duplicates:** a school counts as already registered if it has the same `government_code`, or the same
name in the same LGA. Case, punctuation and accents are ignored.

## Command line (for the implementation team)

```bash
python -m syntegra.cli seed-structure ng-abia                      # once: Abia State, ASUBEB + 17 LGEAs, Ministry of Tertiary Education
python -m syntegra.cli import-institutions schools.csv --board-code ASUBEB                       # check only
python -m syntegra.cli import-institutions schools.csv --board-code ASUBEB --commit
python -m syntegra.cli import-institutions tertiary.csv --board-code MIN-TERTIARY-EDUCATION --commit
```

## Private schools

Private schools are **not** imported by the state. Following the state directive, each proprietor
registers their own school
([ADR-014](../adr/ADR-014-private-school-registration.md)):

1. **The proprietor applies** online with the school's details, using the same fields as above,
   including the full address and postcode, plus their own name, email, phone and CAC number. They
   receive a reference such as `PSR-2026-000123` and can check its status with it and their email.
   The form includes a quick human check (CAPTCHA).
2. **The proprietor confirms their email** by opening the link emailed to them within 48 hours. Until
   they do, the application waits and is not reviewed. They can ask for a new link from the status page.
3. **ASUBEB reviews** the application, inspects the site, then approves or rejects it with a reason.
4. **On approval**, the school appears in the state register as a private school under its LGEA, and the
   proprietor signs in with their email to set it up.
