# ADR-001: Web stack

**Status:** Proposed. Confirmation needs the two-week comparison spike against Django + DRF (§10).

## Decision (candidate)
FastAPI (ASGI) on Uvicorn, SQLAlchemy 2 async ORM, Pydantic v2 contracts, Alembic migrations.

## Evidence from Phase 0
* Tenant/scope enforcement fits SQLAlchemy's `do_orm_execute` and `with_loader_criteria` well (§10.3).
* Pydantic models drive OpenAPI 3.1 directly (`/openapi.json`).
* Lambda-based loader criteria were rejected: their statement caching mishandled the bound tenant values,
  so the filter applies plain per-entity criteria (`platform/session.py`).
