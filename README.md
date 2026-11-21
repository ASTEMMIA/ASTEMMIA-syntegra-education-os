# Syntegra Education OS: Phase 0 Foundations

A Python modular monolith built from **EdTech-003, the Canonical Enterprise Data Architecture (CEDA)**.
This repository delivers the start of **Phase 0 (Foundations)** from CEDA §18. Every later module
depends on these packages.

| Package | CEDA | What it does |
|---------|------|--------------|
| `syntegra.core` | §6.1, §7.1, §9.1, §20 | UUID v7 IDs, ISO 7064 check digits (LRN, STF, INS, certificate, DOC), money in minor units, RFC 9457 errors, the event envelope (rejects personal data), audit hash-chain maths, request context |
| `syntegra.governance` | §2, §11.2, §29 | Tenants and the 10-level scope hierarchy (materialised path plus `scope_closure`), configuration resolved nearest-scope-wins with locks (P4), and the **policy rule DSL**: a sandboxed AST compiler with publish-time tests, tighten-never-relax checks and conflict detection |
| `syntegra.iam` | §8.1–8.2, §21 | The §21 role matrix as data. Grants are role × scope × validity × clearance cap. Decisions are the union over ancestor scopes. Custom roles can never exceed their creator. |
| `syntegra.mdm` | §4, §6.2, §8.1, §16.3 | **Person 360**: one record per human, with explainable duplicate matching (name variants, swapped names or dates, shared IDs and phones), reversible merges with source-trust survivorship, AES-256-GCM-encrypted national IDs found by blind index, purpose-bound audited reveal, and a data-quality score |
| `syntegra.institutions` | §4, §10.4 | **School register**: each government school under its LGEA in ASUBEB, inside one Abia State tenant from kindergarten to university (ADR-010), with bulk import from CSV (preview → commit) and a duplicate check. See the [import guide](docs/guides/school-import.md). |
| `syntegra.workflow` | §22 | Versioned definitions, transitions, four-eyes approval, working-day SLAs with a reminder at 75% |
| `syntegra.platform` | §10.3, §11 | MySQL types (BINARY(16) UUIDs, DATETIME(6) UTC), the §6.1 standard columns, **tenant and scope isolation on every ORM query**, refusal of cross-tenant and out-of-scope writes, P7 immutability, **automatic hash-chained audit in the same transaction**, and the transactional outbox with an ordered relay |
| `syntegra.web` | §10.4 | FastAPI: Problem Details with `next_action`, required `Idempotency-Key` on POST, cursor pagination, permission checks |

Module boundaries are enforced by import-linter (§10.2). Domain packages import no framework.

## Platform baseline (ADR-002, ADR-006)
| Layer | Version |
|-------|---------|
| Linux | RHEL 10 hosts; app image on Red Hat UBI 10 (`ubi10/ubi-minimal:10.2`); MySQL image on Oracle Linux 9 |
| Python | 3.14 from Red Hat (RHEL 10 AppStream); 3.15 also tested in CI |
| MySQL | 9.7 LTS (production target, RDS Multi-AZ); 26.x Innovation tested as an early warning |

## Run it

```bash
uv sync
uv run pytest -q                       # SQLite
docker compose up -d mysql mysql-grants
export SYNTEGRA_DATABASE_URL=mysql+asyncmy://syntegra:syntegra@127.0.0.1:3306/
uv run alembic upgrade head
SYNTEGRA_TEST_DATABASE_URL=$SYNTEGRA_DATABASE_URL uv run pytest -q   # full suite on MySQL 9.7
uv run python -m syntegra.cli bootstrap --admin admin@example.org
uv run python -m syntegra.cli seed-structure ng-abia --admin admin@abia.example   # Abia State from its structure file (ADR-010, ADR-011)
uv run python -m syntegra.cli import-schools schools.csv [--commit]     # register schools from CSV
uv run uvicorn syntegra.web.app:create_app --factory --reload        # http://localhost:8000/docs
```

In dev, sign in with the development login (ADR-007), then send the access token on every request:

```bash
curl -s -X POST localhost:8000/v1/auth/dev-login -H 'content-type: application/json' \
  -d '{"tenant_code":"SYNTEGRA","login":"admin@example.org"}'
curl -s localhost:8000/v1/me -H "Authorization: Bearer <access_token>"
```

Access tokens last 10 minutes. Exchange the refresh token at `POST /v1/auth/refresh`, and sign out with
`POST /v1/auth/logout`. The dev login does not exist outside dev/test, and the server refuses to start
there without a real signing key.

## Quality gate (CI)
`ruff` → `mypy --strict` (domain) → `lint-imports` → generated DDL is current (`schema/ceda/*.sql`) →
tests on SQLite → `alembic upgrade` + `alembic check` and tests on MySQL 9.7 LTS (Python 3.14 and 3.15) with an 80% coverage gate → the same tests inside RHEL 10 UBI with Red Hat's Python 3.14 → build and smoke-test the UBI image. MySQL 26.x Innovation runs as a non-blocking early-warning job.

## Decisions
See [`docs/adr`](docs/adr/README.md). Start with ADR-000 (why Phase 0 starts before artefact
approval) and ADR-005 (what is still open in Phase 0).
