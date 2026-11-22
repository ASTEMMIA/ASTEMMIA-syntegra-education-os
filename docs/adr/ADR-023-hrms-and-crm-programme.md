# ADR-023: Full HRMS and CRM for the education platform: scope and phases

**Status:** Accepted 2026-11-22 (the programme). Each phase gets its own ADR as it is built.

## Context
The platform must manage educational staff in full depth: every teacher and non-teaching staff member in
government and private institutions, from recruitment to exit, including payroll. It must also manage
relationships: staff, parents and the community, partners, donors and vendors, and recruitment candidates.

What CEDA v1.0 covers:
* **HR:** Staff 360 (§4) and HR and payroll entities (§27.1): vacancies and applications,
  safer-recruitment checks, contracts, leave and absence, CPD, appraisal and observation, payroll runs,
  payslips, deductions and statutory returns (PAYE, PenCom, NHF), and TRCN registration.
* **CRM:** nothing.

## Decisions (agreed 2026-11-22)
* **The CRM covers all four relationship groups:**
  * staff relations and helpdesk;
  * parents and community;
  * partners, donors and vendors;
  * recruitment candidates.

  The fifth group, "something else", is the platform's own course and content architecture (W1).
* **Payroll is full payroll for all institutions,** government and private, on the platform:
  * salary structures by grade level and step;
  * allowances and deductions: PAYE, PenCom pension, NHF;
  * payslips and bank schedules;
  * four-eyes approval, and posting to the ledger.
* **Build order starts with Staff 360 and postings,** because every other part depends on it.

## Phases
| # | Phase | Contents |
|---|-------|----------|
| H1 | **Staff 360 and postings** | Staff record (1:1 with Person 360), staff number, teaching or non-teaching, employer, government file number; posts at institutions or offices with position, grade level, step and salary structure; postings, transfers and promotions with four-eyes approval and history; qualifications (verified); TRCN registration; retirement due date (configurable; defaults below); nominal roll and staffing summaries; bulk import of the nominal roll; HR officer role. |
| H2 | Recruitment and onboarding | Vacancies, applications, interviews, offers; safer-recruitment checks (identity/NIN, police record, qualifications, health, references, right to work), no start date without them; onboarding checklist. Candidate CRM (C4) shares the talent pool. |
| H3 | Leave, absence and attendance | Leave types and entitlements, requests and approvals, absence and cover, staff attendance; leave without pay feeds payroll. |
| H4 | Performance and development | Appraisal (including APER-style annual reports), lesson observation, CPD hours and TRCN renewal, training records. |
| H5 | Payroll | Salary structures and scales, allowances, deductions, PAYE, PenCom, NHF, loans; payroll runs with four-eyes approval; payslips; bank schedules; statutory returns; ledger postings (§25). |
| H6 | Discipline, welfare and exits | Queries and disciplinary cases, grievances (with CRM C1), exits (resignation, retirement, death, dismissal), clearance, final pay, records retention. |
| C1 | Staff relations and helpdesk | Cases and tickets (pay queries, HR requests, grievances) with categories, SLAs and assignment; communications to staff; interaction history. |
| C2 | Parents and community | Enquiries, complaints and cases from parents and the public; admissions enquiries; interaction history linked to Person and Family 360. |
| C3 | Partners, donors and vendors | Organisations and contacts, interactions, partnership and sponsorship pipelines, vendor records (with procurement, §25). |
| C4 | Recruitment candidates | Talent pool, outreach, candidate pipeline (shared with H2). |
| W1 | Course and content architecture, inside the platform (not a public website) | UX and UI: a clear navigation hierarchy that surfaces courses, projects and community to signed-in users. A course library that grows: each course has a detail page, curriculum and learning outcomes (Curriculum 360 and LMS, CEDA §24). Fast pages, and search within the platform. |

**Common to every phase:**
* tenant and scope isolation, audited changes, events, and permissions by role;
* bulk import where lists exist;
* staff self-service and manager approval as each phase lands.

**Defaults to confirm with Abia State.** Retirement for teachers is 65 years of age or 40 years of
service (the Harmonised Retirement Age for Teachers in Nigeria Act 2022). For other staff it is 60 years
or 35 years of service. Both are configuration (`hr.retirement`), not code.

## Consequences
* Payroll for government staff runs on the platform. Its relation to the state's existing payroll
  (parallel run, cut-over, exports) is planned in H5 with the Abia State Ministry of Finance and Head of
  Service.
* CEDA §27 is extended phase by phase, and §34 records progress.
