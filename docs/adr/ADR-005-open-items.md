# ADR-005: Phase 0 open items

Phase 0 scope (§18) that this baseline does not yet deliver:

| Item | CEDA | Notes |
|------|------|-------|
| Passkeys/FIDO2, risk-based step-up, SCIM, KMS-held signing key | §8.3, §10.1 | Token issuance and rotation (ADR-007), OIDC single sign-on and TOTP MFA with recovery codes (ADR-016) are done. Passkeys need the web front end; SCIM provisioning and a KMS-held token signing key remain. |
| MDM: KMS key provider, NIMC adapter (cross-tenant identity for government schools resolved by ADR-009) | §4, §6.2 | Person 360 with matching, merge, encryption and data quality is done (ADR-008); these items remain. |
| Code lists (ISO 3166/639/4217, ISCED) | §7.2 | Versioned `code_list` and `code_value` |
| Break-glass access, consent and processing purpose | §8.2, §8.4 | |
| Workflow persistence and SLA scheduler | §22 | The domain model and catalogue are in place; tables and Celery jobs are not. |
| MSK publisher and Glue schema registry | §10.1, §20 | The outbox relay works with any `Publisher`. |
| Audit day close, S3 Object Lock anchor, nightly verifier alerting | §9.1 | `cli verify-audit` exists |
| Read-audit of restricted and special-category data | §9.1 | `append_audit` is ready to call from read paths |
| Design system and view-model envelope | §14 | |
| Terraform modules, multi-AZ, DR | §15 | |
