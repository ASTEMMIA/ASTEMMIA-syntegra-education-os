# Registering ASUBEB schools from a spreadsheet

All Abia State government kindergarten (ECCDE), primary and secondary schools are registered under ASUBEB,
one of the boards inside the Abia State organisation
([ADR-010](../adr/ADR-010-abia-state-kindergarten-to-university.md)). Work as an ASUBEB user, with ASUBEB as
your active scope, when you upload. Load them from one spreadsheet saved as **CSV (UTF-8)**.

## Columns

| Column | Required | Example | Notes |
|---|---|---|---|
| `name` | yes | Community Primary School Ndume | 3–200 characters |
| `lga` | yes | Umuahia North | One of Abia's 17 LGAs. "Umuahia North LGEA" and "umuahia north l.g.a." also work. |
| `levels` | yes | Primary · JSS; SSS · Secondary | Primary, JSS, SSS, ECCDE. "Pry", "JSS & SSS" and "Secondary" (= JSS + SSS) are understood. |
| `government_code` | no | ASB/UN/001 | ASUBEB's own school number. Strongly recommended: it is the surest way to avoid duplicates. |
| `town`, `address` | no | Ndume | |
| `phone`, `email` | no | 0803 123 4567 | Phone numbers are stored in international form (+234…). |
| `latitude`, `longitude` | no | 5.5320, 7.4860 | Both or neither. |

Download the template from the system (`GET /v1/bulk/institutions/template`), or copy the header row above.

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
python -m syntegra.cli import-schools schools.csv --board-code ASUBEB            # check only
python -m syntegra.cli import-schools schools.csv --board-code ASUBEB --commit
```
