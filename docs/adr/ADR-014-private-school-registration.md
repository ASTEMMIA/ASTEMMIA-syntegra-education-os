# ADR-014: Private school registration

**Status:** Accepted 2026-11-22. The regulating body, ASUBEB, was confirmed by the programme owner on 2026-11-22.

## Context
The state government has directed that **every private school provider must register their school on the
system**. CEDA:
* **§2:** private institutions are **full tenants** (their own branding, fees, workflows and roles) with
  a **regulatory link** to their state scope
* **§22:** defines the **institution registration** workflow: application → desk review → site
  inspection → decision → licence → Institution 360 active
* **§29.3:** a licence gives the category and levels

## Decision
**1. Self-service application, no account needed.**
* Endpoint: `POST /v1/public/{jurisdiction}/private-school-applications`.
* **The school's details** are validated exactly like an import row for the regulator's body: type,
  levels, LGA, full address and postcode (ADR-012, ADR-013).
* **The proprietor's details:** name, email (it becomes the school's sign-in), phone, and an optional
  CAC number (RC/BN/IT).
* **The applicant gets a reference** (`PSR-YYYY-NNNNNN`) and checks status with the reference plus the
  proprietor's email. Without both, nothing is disclosed.
* **Refused:** a school already registered, or already applied for (same name and LGA, application
  still open). Re-application is allowed after a rejection.

**2. Review by the regulator.**
* Endpoints: `GET /v1/private-school-applications`, and `POST …/{reference}/transition`, which needs
  `institution:edit` at the regulator's scope.
* **Workflow:** submitted → under review (10 working days) → site inspection (20) → approved. An
  application can be rejected at any stage with a reason, which the proprietor sees.
* **Approval cannot skip** review and inspection (workflow engine, `institution_registration`).
* **Every step is recorded:** in the application's history, the audit chain, and an event
  (`INS.APPLICATION_*`).

**3. Approval registers the school, re-checking its details first.**
* **Its own tenant:** institutional and private, with its root **under its LGEA** inside the regulator.
  This is the regulatory link, so the state's grants cover it.
* **A state-register entry:** `org.institution` with ownership `private`, `own_tenant_id`, the proprietor
  and the full address. The state sees it in the register alongside government schools; the school's
  operational data stays in its own tenant.
* **The proprietor's account:** an institution-administrator account in that tenant, so they can set up
  their school.

**4. Which body regulates, and for which levels, is configuration** (`regulates_private` per body in
the structure file, which becomes `private.registration`). Abia: **ASUBEB, ECCDE to SSS**, following the
direction that primary and secondary schools sit under ASUBEB, **confirmed by the programme owner**. Private
universities are licensed federally (NUC), so tertiary is not offered for private registration here.

## Security
The public endpoints are unauthenticated by design.
* **Disclosure:** they return only a reference or a status.
* **Duplicates:** repeat submissions are refused.
* **CAPTCHA and email verification:** now in place ([ADR-015](ADR-015-registration-safeguards.md)).
* **WAF rate limiting:** remains an infrastructure item.

## Schema
Migration 0007:
* new table `org.registration_application`
* new columns `institution.own_tenant_id` and `institution.proprietor_name`

Upgrade, check and downgrade were tested on MySQL.
