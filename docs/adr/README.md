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
| [008](ADR-008-person-master-record.md) | Person master record: matching, merge, encryption, data quality | Accepted (cross-tenant identity open) |
| [009](ADR-009-asubeb-state-tenancy.md) | Government schools under ASUBEB; duplicate reviews routed to the LGEA or state | Accepted (tenancy superseded by 010) |
| [010](ADR-010-abia-state-kindergarten-to-university.md) | One Abia State tenant, kindergarten to university: ASUBEB and the Ministry of Tertiary Education as scopes | Accepted |
| [011](ADR-011-jurisdiction-structure-files.md) | Jurisdiction structure files: every state or country is data, not code | Accepted |
| [012](ADR-012-tertiary-register.md) | One institution register, kindergarten to university: tertiary institutions under the Ministry | Accepted |
| [013](ADR-013-institution-address.md) | Full postal address and postcode for every institution, formats from the structure file | Accepted |
| [014](ADR-014-private-school-registration.md) | Private school registration: public application, regulator review, own tenant on approval | Accepted (ASUBEB confirmed as regulator) |
| [015](ADR-015-registration-safeguards.md) | CAPTCHA and email verification on public forms; transactional email outbox | Accepted (SES sender: ADR-017) |
| [016](ADR-016-single-sign-on-and-mfa.md) | Single sign-on through OpenID Connect; MFA with authenticator apps and recovery codes | Accepted (passkeys open) |
| [017](ADR-017-amazon-ses-email.md) | Email delivery through Amazon SES: failure handling, backoff, set-up | Accepted (bounce processing: ADR-018) |
| [018](ADR-018-ses-bounce-and-complaint-processing.md) | SES bounces, complaints and deliveries: event queue, suppression list, delivery status on applications | Accepted |
