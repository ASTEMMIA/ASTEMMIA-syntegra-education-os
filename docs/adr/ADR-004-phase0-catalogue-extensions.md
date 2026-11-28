# ADR-004: Phase 0 additions to the event and permission catalogues

**Status:** Proposed

§20 and §21 do not cover the governance and IAM administration that Phase 0 needs. These additions follow
the existing naming rules:

**Events:** `GOV.TENANT_CREATED`, `GOV.SCOPE_NODE_CREATED`, `GOV.POLICY_PUBLISHED`, `GOV.POLICY_WITHDRAWN`,
`IAM.USER_CREATED`, `IAM.GRANT_ADDED`, `IAM.GRANT_REVOKED`, `IAM.ROLE_CLONED`,
`IAM.SESSION_STARTED`, `IAM.SESSION_REVOKED`, and the `PER.*` person events (ADR-008). Payloads carry IDs only.

**Role:** `PADM` Platform administrator (governance level 1).

**Permissions:** `tenant:manage` (PADM, FED, STA), `scope_node:manage` (PADM, FED, STA, IADM, HEAD), and
`person:view|create|edit|merge` (ADR-008).

The 25 rows of §21 are transcribed unchanged in `iam/baseline.py`. Tests spot-check them.
