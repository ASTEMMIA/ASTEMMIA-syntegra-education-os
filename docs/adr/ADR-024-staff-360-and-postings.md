# ADR-024: Staff 360 and postings (HRMS phase H1)

**Status:** Accepted 2026-11-22. Retirement defaults to be confirmed with Abia State.

## Context
ADR-023 sets out the full HRMS. Every later phase (recruitment, leave, appraisal, payroll, exits)
hangs off one staff record, so it comes first. CEDA §4 (Staff 360) and §27.1 describe it.

## Decision
**Tables (`acad` schema, migration 0015):**
* **`staff`:** one person's employment with an employer (a tenant), 1:1 with Person 360, holding:
  * the staff number `STF-NNNN-NNNN-CC` (ISO 7064 check digits) and the employer's own file number;
  * the category (teaching or non-teaching) and status (active, suspended, on leave, exited);
  * the dates of first appointment, confirmation and exit, and the exit reason.
* **`staff_post`:** where someone works and as what.
  * **The place:** an institution or an office such as an LGEA.
  * **The post:** position, grade level (1-17) and step (1-15), and the salary structure: CONPSS,
    CONTISS and so on, or PRIVATE.
  * **The terms:** primary or not, the share of full-time hours, the appointment type, the start and
    end dates, and the posting letter reference.
  * Posts are never overwritten. A posting ends the old post the day before the new one starts.
* **`staff_qualification`:** the kind (PhD down to certificate), title, awarding body and year.
  * A qualification counts as a teaching qualification if it is an NCE or PGDE, or a degree in
    education (B.Ed, B.Sc(Ed), M.Ed).
  * It counts once verified, and nobody can verify their own.
* **`teacher_registration`:** registration with a teaching council (TRCN by default, checked against
  the education body register, ADR-019), with number, status and expiry. One number belongs to one
  staff member.
* **`posting_request`:** a proposed posting, transfer, promotion, acting appointment, secondment or
  redeployment, with effective date, reason and reference.

**Rules:**
* **Four-eyes.** A second officer with `staff:post` decides every posting, and a rejection needs a
  reason. The requester can cancel their own request. A staff member can have one pending request at
  a time.
* **Sensible postings:**
  * A promotion needs a higher grade level.
  * A transfer or posting needs a different place.
  * The effective date must follow the current post's start.
  * A first appointment is made by appointing, not posting.
* **Visibility follows the person.** The staff record, qualifications and registrations take the
  scope of the current primary post. A school sees the staff posted to it; the LGEA, board and state
  see everyone beneath them; and an approved transfer moves visibility to the new school. Old posts
  stay visible where they were held.
* **Identity stays on Person 360.** Whoever may see a staff record sees that person's name, sex and
  date of birth with it. This is a sanctioned read limited to the tenant and those people. The rest of
  Person 360 still needs `person:view`.
* **Retirement due date:** the earlier of the age limit and the service limit. The defaults can be
  changed per jurisdiction through the `hr.retirement` setting:

  | Staff | Age | Years of service | Source |
  |-------|-----|------------------|--------|
  | Teaching | 65 | 40 | Harmonised Retirement Age for Teachers in Nigeria Act 2022 |
  | Non-teaching | 60 | 35 | Public service rule |

* **Qualified teacher:** a verified teaching qualification and a current TRCN registration.
* **Exit** (resignation, retirement, death, dismissal, termination, end of contract, transfer out):
  ends every current post and cancels pending postings. The record is kept.

**Nominal roll and reports:**
* **The roll** (`GET /v1/staff`) filters by institution, category, status, position, grade range,
  retirement within N days, and teachers without TRCN registration. It can be downloaded as CSV.
* **The staffing summary** gives, per place of work: teaching and non-teaching numbers, qualified
  teachers, TRCN-registered teachers, and staff retiring within a year.

**Roles and permissions:**
* **New permissions:** `staff:view`, `staff:edit` and `staff:post`.
* **New role:** HR officer (HRO), with staff view, edit and posting rights plus Person 360
  registration.
* **Existing roles extended:**
  * institution administrators: view and edit staff;
  * heads: view staff;
  * state officers: view staff, and request and decide postings.
* **Existing databases** get the new role and permissions with `syntegra sync-roles`.

**Events:** `STF.APPOINTED`, `STF.POSTED`, `STF.POSTING_REQUESTED`, `STF.POSTING_DECIDED`,
`STF.QUALIFICATION_VERIFIED`, `STF.REGISTRATION_RECORDED` and `STF.EXITED`.

## Consequences
* **Payroll (H5) will read the grade level, step and salary structure from the current post.** Leave
  (H3) and appraisal (ADR-025) attach to the staff record.
* **Open in H1:**
  * a bulk import of the existing nominal roll (with Person 360 matching) and an Excel template for it;
  * staff self-service views;
  * future-dated postings that take effect automatically. Today a future date ends the old post at
    approval and the new post becomes current on its date.
