# ADR-026: Performance management and continuous professional development

**Status:** Accepted 2026-11-22. The CPD points requirement (default 20 a year) and the appeal period
(default 30 days) are placeholders to confirm with ASUBEB, the Ministry and TRCN.

## Context
The appraisal in ADR-025 was asked to be expanded "to include all considerations, including all
continuous development programmes". This completes HRMS phase H4 (ADR-023).

## Decision
**1. Performance objectives (Performance Management System)** (`acad.performance_objective`)
* **Agreed for each cycle:** the appraiser agrees objectives with each staff member: title, measure
  (KPI), target and weight. Weights add up to at most 100.
* **Mid-year review:** progress as a percentage, with a note.
* **Year-end assessment:** achievement as a percentage, with a note. An objective that no longer
  applies can be dropped with a reason.
* **Score blending:** a cycle's `objectives_weight` (0 to 70%) blends objectives into the overall
  score. For example, at 30%: 0.7 × criteria score + 0.3 × weighted achievement.
* **Assessed before submitting:** when objectives count, every agreed objective must be assessed
  before the appraisal is submitted.
* **The record keeps both parts:** the criteria score and the objectives score are stored alongside
  the overall score.

**2. Self-assessment.** The appraised staff member rates themselves on the same criteria, while the
appraisal is in draft. Their ratings are kept beside the appraiser's. Only that person can do it, and
reasons for extreme ratings are optional for them.

**3. Probation and confirmation reviews.** An appraisal can be of kind `probation` for staff not yet
confirmed. It must recommend confirm, extend or terminate (and annual appraisals cannot). It is not
blended with objectives. When a recommendation to confirm is countersigned, the staff member's
`confirmation_date` is set (`STF.APPOINTMENT_CONFIRMED`). Ending an appointment remains HR's act
(ADR-024 exit).

**4. Appeals** (`acad.appraisal_appeal`)
* **Who and when:** only the appraised person can appeal, once, against a countersigned appraisal,
  within the appeal period (setting `hr.appraisal.appeal_days`, default 30).
* **Who decides:** an officer with `appraisal:countersign` who was neither the appraiser nor the
  countersigning officer.
* **The decision:**
  * **upheld:** may revise ratings; the score is recalculated the same way (`revised_score` and
    `revised_band`);
  * **dismissed:** the original stands. Either way, reasons are required.
* **The record:** the appraisal shows the `final_score` and `final_band` that stand.

**5. CPD programmes** (`acad.cpd_programme`)
* **Created by a body:** ASUBEB, the Ministry, Abia-SAME or a school.
* **What each records:**
  * the code, title, provider, and whether the provider is accredited;
  * the category and mode, the audience, the dates and venue;
  * the hours, CPD points, capacity and status.
* **Categories:** subject knowledge, pedagogy, ICT, leadership, assessment, inclusive education,
  safeguarding, early years, literacy and numeracy, wellbeing, other.
* **Modes:** in person, online, blended, school-based.
* **Visibility:** schools beneath a body see its programmes and nominate to them; the body sees what
  is run beneath it.
* **Cancelling** withdraws open nominations.

**6. Taking part** (`acad.cpd_participation`)
* **Nominations:** nominated → enrolled → attended → completed (on or after the last day, not later
  than today, with evidence) → verified; or withdrawn at any step before completion.
  * Nominations respect capacity, need active staff, and allow no duplicates.
  * A nomination can cite the appraisal that recommended training.
* **Self-reported CPD:** staff report what they did elsewhere: workshop, course, conference,
  mentoring, peer observation, research, or a qualification. Evidence is required, and a reflection on
  what changed in their practice is optional.
* **Verification:** another person verifies (and may adjust the points) or rejects with a reason.
  Nobody verifies their own. Only verified CPD earns points.

**7. Compliance.** Verified CPD points and hours per staff member in a calendar year, against the
annual requirement (setting `hr.cpd.annual_points`, default 20, to confirm against TRCN's mandatory
CPD). It gives the shortfall per person and the number meeting the requirement per place of work.
This is the evidence TRCN registration renewal needs.

**8. Individual development plans** (`acad.development_action`)
* **Each action records:** a need, an action, a CPD category, a due date and a status (planned, in
  progress, done or dropped).
* **Done or dropped** needs the outcome or reason.
* **Links:** to the appraisal it came from, and to the CPD record that met it.
* **Overdue actions are flagged.**

**9. Training-needs analysis.** From the countersigned appraisals in a cycle within the caller's scope:
* which criteria were rated Poor or Fair, at which places;
* the CPD category that answers each (for example, classroom management → pedagogy, assessment →
  assessment);
* totals by category, and how many appraisals recommended training.

Programmes are then planned where they are needed.

**Roles and permissions:**
* **New permissions:** `cpd:view` and `cpd:manage`.
* **Who holds them:**
  * HR officers, state officers, institution administrators and heads: both;
  * inspectors: view.
* **Events:** `STF.APPRAISAL_SELF_ASSESSED`, `STF.APPEAL_LODGED`, `STF.APPEAL_DECIDED`,
  `STF.APPOINTMENT_CONFIRMED`, `STF.CPD_NOMINATED` and `STF.CPD_VERIFIED`.
* **Migration 0016:**
  * new tables;
  * `appraisal` gains its kind, self-assessment and the two score parts, with existing appraisals
    becoming annual;
  * widened recommendation values.

## Consequences
* **Appraisal, objectives, development plans and CPD now form one loop:**
  1. Appraisal shows the need.
  2. The development plan and nominations answer it.
  3. Verified CPD counts toward compliance.
  4. The next appraisal's "professional development" criterion has the evidence.
* **Open:**
  * reminders and SLAs for appraisal stages and development actions (with the workflow engine);
  * linking CPD records to TRCN renewal submissions;
  * 360-degree feedback from colleagues or learners, if the state wants it;
  * printed appraisal and CPD certificates.
