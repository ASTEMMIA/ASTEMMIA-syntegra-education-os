# ADR-009: Government schools in one ASUBEB tenant

**Status:** Accepted 2026-11-21

## Context
All Abia State government primary and secondary schools will be registered on the system **under the Abia
State Universal Basic Education Board (ASUBEB)**. This was the programme owner's direction.

ADR-008 left one item open: identity for a learner who moves between institution tenants. Matching runs
within one tenant, so if every school were its own tenant, a child moving schools would be registered
twice.

## Decision
The governance hierarchy (CEDA §2, Nigeria column) for government schools is:

```
Syntegra (global) → Nigeria (country tenant) → ASUBEB (regional tenant, level 3)
  → 17 LGEAs (level 4 scope nodes) → each government school (level 6 scope node) → campus/unit/class
```

* **Single tenant:** ASUBEB is the single tenant for all government primary and secondary schools.
  Schools are **scope nodes inside it**, not separate tenants.
* **Private schools** remain full tenants with a regulatory link to the state scope (§2), unchanged.
* **Access follows the hierarchy through grants:**
  * a head or registrar is granted at their school node
  * an LGEA officer at the LGEA node
  * ASUBEB staff at the root
* **Consequences for Person 360 (ADR-008):**
  * One person record per child statewide. A move between government schools keeps the same record and
    Global Learner ID; nothing is re-registered.
  * Duplicate detection covers every government school in the state. Records in other schools stay hidden
    (P6): the caller sees only "a possible match exists elsewhere".
  * **Review routing:** a duplicate review between two records the caller cannot both see is filed at the
    **lowest scope that contains both**: the LGEA when the schools share one, otherwise ASUBEB. Stewards
    at that level, and only they, see both records. This replaces "file at the tenant root", which at
    state scale would put every cross-school review into one statewide queue.
* **Setup:** `python -m syntegra.cli seed-asubeb [--admin <login>]` creates Nigeria, ASUBEB and the
  17 LGEAs, using Abia's local government areas. It is idempotent.
* **Scale:** within the §11.3 sizing basis of 5M learners per region.

## Notes
* If senior secondary schools are later placed under a different board, that board becomes a sibling
  regional tenant or a scope under the state, by configuration. No code change.
* The official list of government schools (name, code, LGA, levels) is the input for the school
  register and bulk import, which are the next work. School counts are taken from that list, not assumed.

## Next
1. **School register (Institution 360):** done. `org.institution` and `org.institution_level` (migration
   0004). Each school's scope node and record are created together, with an `INS-NG-NNNNNN` ID from a
   locked, never-reused sequence and an `INS.REGISTERED` event.
2. **Bulk import:** done for schools (`ops.bulk_job`, preview → commit, CLI `import-schools`;
   [guide](../guides/school-import.md)). Staff and learner imports follow the same pattern, with the
   duplicate check on every learner row.
3. **Bulk staff grants** from the staff list (heads, teachers, registrars, LGEA officers).
4. **Rollout waves** by LGEA, tracked on the state command centre.
