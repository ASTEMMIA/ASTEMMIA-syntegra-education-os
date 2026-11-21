# Architecture Decision Records

Every significant decision ships as an ADR plus a CEDA minor version (EdTech-003 §0).

| ADR | Title | Status |
|-----|-------|--------|
| [000](ADR-000-phase0-build-start.md) | Phase 0 build start ahead of artefact approval | Proposed |
| [001](ADR-001-web-stack.md) | Web stack: FastAPI + SQLAlchemy 2 + Pydantic v2 | Proposed (spike pending) |
| [002](ADR-002-mysql-engine.md) | Transactional store: MySQL 9.7 LTS on RDS Multi-AZ | Proposed (CI on 9.7 + load/failover pending) |
| [003](ADR-003-audit-chain.md) | Audit chain mechanics | Proposed |
| [004](ADR-004-phase0-catalogue-extensions.md) | Phase 0 additions to the event and permission catalogues | Proposed |
| [005](ADR-005-open-items.md) | Phase 0 open items | Open |
| [006](ADR-006-enterprise-platform-baseline.md) | Enterprise baseline: RHEL 10 UBI, Red Hat Python 3.14, MySQL edition | Accepted (RDS for MySQL 9.7 Community) |
| [007](ADR-007-sign-in-and-tokens.md) | Sign-in and tokens: dev login, ES256 access tokens, rotating refresh tokens | Accepted (OIDC + MFA open) |
