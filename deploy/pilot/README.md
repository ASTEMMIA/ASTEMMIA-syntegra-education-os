# Pilot deployment (ADR-031)

The list of pilot institutions is **confidential until it is formally approved**, so it is not in
this repository. At deployment, put it in this folder as `pilot-institutions.csv` (git ignores
every CSV here except the example) and load it:

```bash
python -m syntegra.cli pilot-load deploy/pilot/pilot-institutions.csv --tenant-code NG-AB
python -m syntegra.cli pilot-demo deploy/pilot/pilot-institutions.csv --tenant-code NG-AB --learners 2500
```

* The file is the institution template with a `board` column: `ASUBEB` registers primary and
  secondary schools; `MIN-TERTIARY-EDUCATION` registers technical colleges and polytechnics;
  `ABIA-SAME` adult learning centres. `latitude` and `longitude` place the school on the
  monitoring dashboard's map.
* Every row is checked first; one problem (an LGA that does not exist, say) stops the whole
  load. Running it again finds the institutions already registered.
* `pilot-demo` adds made-up learners, guardians, teachers and classes, all marked as
  **demonstration data** on screen and on the dashboard. It refuses to run in production
  (`SYNTEGRA_ENV=prod`) and refuses to run twice.
