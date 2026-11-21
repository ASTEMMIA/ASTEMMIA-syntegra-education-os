# ADR-010: One Abia State tenant, from kindergarten to university

**Status:** Accepted 2026-11-21. Supersedes the tenancy part of ADR-009.

## Context
The programme owner's direction is that the platform is **the one-stop repository for all educational
matters in Abia State, from kindergarten to university**:
* government ECCDE/kindergarten, primary and secondary schools are registered under **ASUBEB**
* post-secondary institutions are registered under the **Abia State Tertiary Education Board**

ADR-009 made ASUBEB its own tenant. A second board as a second tenant (option B) would create a new
person record when a school leaver enters a polytechnic or university. That breaks P1 ("one record,
many uses") and the lifelong learner record of P3. The programme owner chose **option A**.

## Decision
```
Syntegra → Nigeria (country tenant) → Abia State (regional tenant)
             ├── ASUBEB (board)            → 17 LGEAs → government ECCDE, primary and secondary schools
             └── Tertiary Education Board  → polytechnics, colleges of education, university
```

* **One tenant for the state.** Each board is a **scope inside it**. Each board's staff are granted at
  their board, so each board sees only its own institutions; state officers granted at the state root
  see both.
* **One Person 360 record for life.** A child registered at a kindergarten keeps the same record and
  Global Learner ID through primary, secondary and tertiary education. Duplicate detection spans the
  whole state, and records outside the caller's scope stay hidden (P6).
* **Level numbers are only a ranking** (child below parent); what users see comes from `local_label`:
  * the state is REGION (3)
  * a board is LOCAL_AUTHORITY (4), labelled "Board"
  * an LGEA is NETWORK (5), labelled "LGEA"
  * an institution is INSTITUTION (6)

  The blueprint makes the hierarchy configurable per jurisdiction (§2), so code must not treat a level
  number as meaning "LGA": the school import finds LGEAs as the children of the board the user is
  working in.
* **Which levels a board registers is configuration** (`register.levels`, P4), set on the board. ASUBEB:
  ECCDE, primary, JSS, SSS. A row for another level is rejected with "this board does not register …".
* **Duplicate reviews** still go to the lowest scope containing both records (ADR-009): the LGEA, else
  ASUBEB, else the state for cross-board cases.
* **Setup:** `python -m syntegra.cli seed-abia [--admin <login>]`. `seed-asubeb` is kept as an alias.
  It creates Nigeria, Abia State, both boards, the 17 LGEAs under ASUBEB and ASUBEB's register levels,
  and is idempotent.
* **Import:** `import-schools FILE --board-code ASUBEB [--commit]`, or through the API with the board
  as the active scope.

## Open
* **The Tertiary Education Board's official name and abbreviation** are to be confirmed. The working name
  is "Abia State Tertiary Education Board", code `TERTIARY-BOARD`.
* **Tertiary institution register (next):**
  * tertiary levels (TVET, NCE, ND/HND, degree, postgraduate; §23)
  * institutions placed directly under the board, with their location LGA recorded separately from
    their governance parent
  * the same preview → commit import
* **Private schools** remain separate tenants with a regulatory link to the state (§2), unchanged.
