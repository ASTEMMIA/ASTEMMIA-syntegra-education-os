# ADR-000: Phase 0 build start ahead of artefact approval

**Status:** Proposed · **Date:** 2026-11-21

## Context
CEDA §18 says no *production module* code starts until the CEDA and the 25 architecture artefacts
named in the Master Update are approved. ADR-001 and ADR-002 also expect short spikes.

## Decision
Start Phase 0 **foundations** now, as an evaluable baseline: the platform kernel (`core`), governance
and tenancy, IAM, the audit chain, the transactional outbox, the policy DSL and the workflow
model. No Phase 1 education module (learners, attendance, billing…) is started.

This code also serves as the ADR-001 spike candidate for FastAPI and as the ADR-002 MySQL functional
evidence. It must not be deployed to production until the approval gate is passed.

## Consequences
* Architecture review can test the CEDA rules (P1–P13) against working code, not only documents.
* If ADR-001 selects Django/DRF, the `core`, `*.domain` and `policy` packages carry over unchanged
  (they are framework-free by import-linter contract). Only `platform` and `web` are rewritten.
