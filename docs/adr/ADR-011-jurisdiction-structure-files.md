# ADR-011: Jurisdiction structure files

**Status:** Accepted 2026-11-21

## Context
Abia State organises education one way:
* basic education under ASUBEB, by LGEA
* higher and tertiary education overseen directly by the Ministry of Tertiary Education

Other Nigerian states, and other countries, organise it differently. Some have a separate senior secondary
board, zones instead of LGEAs, or no state level at all. CEDA principle P4 ("global core, local
configuration") and review question "Is a jurisdiction rule hard-coded?" require these differences to be
**data**.

The first Abia setup command hard-coded the LGAs, the boards and their names. Two tests also showed the
school import assumed the word "LGEA".

## Decision
* **Each jurisdiction is described by a structure file** (JSON, validated on load; `governance/structure.py`):
  * the country
  * an optional state, region or province
  * its **bodies** (board, ministry, commission, authority), each with its name and label, the levels it
    registers, and its local **units** (LGEAs, zones, districts) with their label
* **One generic command** builds any of them: `python -m syntegra.cli seed-structure <file | bundled name>`.
  It is idempotent and the core code is the same for every jurisdiction.
* **Bundled:** `src/syntegra/structures/ng-abia.json`. `seed-abia` is a shortcut for it.
* **Ranks follow from nesting:** jurisdiction tenant, then body, then unit, then institutions (level 6).
  What users see comes from each node's label, never from a level number.
* **The school import matches units through their own label:** "Ohafia" finds "Ohafia LGEA" in Abia, and
  "North" finds "North Zone" in a zoned state.
* **A body set to register no levels (`"registers": []`)** rejects imports. It does not fall back to the
  defaults. This was a latent bug, found and fixed while doing this.
* **Tests** build Abia, a fictional state with SUBEB plus a separate senior secondary board using zones,
  and a country with no state level, all without code changes.

## Abia State (bundled file)
```
Nigeria → Abia State
            ├── ASUBEB (Board) → 17 LGEAs → ECCDE, primary and secondary schools
            └── Abia State Ministry of Tertiary Education (Ministry) → tertiary institutions
```

## Consequences
* **Onboarding another state or country** means writing its file, not changing code. Changes to an
  existing structure (a new LGEA, a renamed body) are applied by re-running with the updated file. Only
  additions are applied automatically; renames and removals need a governed change (future work, ADR
  when needed).
* **Private institutions** stay separate tenants (§2), outside these files.
