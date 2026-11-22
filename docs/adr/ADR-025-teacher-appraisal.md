# ADR-025: Teacher and staff appraisal, with lesson observation

**Status:** Accepted 2026-11-22. Criteria and weights are defaults to agree with ASUBEB and the
Ministry.

## Context
Appraisal is part of HRMS phase H4 (ADR-023). It was brought forward on request, straight after Staff
360. Nigerian public-service practice uses an annual appraisal: the Annual Performance Evaluation Report
(APER), now the Performance Management System. For teachers, the head teacher appraises and a senior
officer countersigns. Lesson observations are the usual evidence.

## Decision
**Appraisal cycles** (`acad.appraisal_cycle`):
* HR opens a cycle for its employer (for example "2026 annual appraisal") with a period.
* Schools beneath the employer appraise within that cycle.
* The cycle's criteria are frozen when it opens, so its results stay comparable.

**The default criteria** (weights add up to 100):

| Teaching staff | Weight | Non-teaching staff | Weight |
|----------------|--------|--------------------|--------|
| Knowledge of subject | 15 | Knowledge of the job | 20 |
| Lesson planning and preparation (lesson notes) | 15 | Quality and accuracy of work | 20 |
| Teaching methods and classroom delivery | 20 | Output and timeliness | 15 |
| Classroom management and learner discipline | 10 | Initiative and problem solving | 10 |
| Assessment, marking and record keeping | 10 | Punctuality and attendance | 10 |
| Learner progress and outcomes | 10 | Working relationships and service | 10 |
| Punctuality and attendance | 10 | Professional conduct and integrity | 10 |
| Professional conduct and teamwork | 5 | Self-development and training | 5 |
| Professional development (CPD) | 5 | | |

A cycle may be opened with its own criteria instead, checked so that codes are unique and weights add
up to 100.

**Ratings and scoring:**
* **Each criterion is rated 1 to 5:** Poor, Fair, Good, Very good, Outstanding.
* **The score** is the weighted percentage. **Bands:** 85 and over Outstanding; 70 Very good; 55 Good;
  40 Fair; below 40 Poor.
* **A rating of 1 or 5 needs a written reason.**

**The appraisal** (`acad.appraisal`), one per staff member per cycle:
1. **The appraiser starts it** and rates. This is normally the head teacher (`appraisal:conduct`).
   * A draft can be saved incomplete, but not with wrong values.
   * Submitting needs every criterion rated.
2. **The appraiser adds:**
   * strengths, areas to improve, a development plan and a comment;
   * a recommendation: promotion (needs Very good or better), commendation, training, or an
     improvement plan (needs the plan written down).
3. **The appraisee may add their own comment** until it is complete.
4. **A different officer countersigns** (`appraisal:countersign`: the HR officer or state officer) or
   returns it with a reason. A returned appraisal goes back to the appraiser to change and resubmit.

**Lesson observation** (`acad.lesson_observation`):
* **What is recorded:** the class, subject and topic.
* **What is rated:** lesson planning, delivery, classroom management and assessment, from 1 to 5.
* **Comments:** strengths and areas to improve.
* **Evidence:** observations within the cycle period appear on the teacher's appraisal.

**Safeguards:**
* nobody appraises, observes or countersigns their own appraisal (checked through the user's linked
  person record);
* the appraiser cannot countersign;
* appraisals live in the staff member's scope.

**Reports:** cycle progress per place of work: staff, not started, draft, submitted, returned,
countersigned, and the average countersigned score.

**Roles and permissions:**
* **New permissions:** `appraisal:view`, `appraisal:conduct` and `appraisal:countersign`.
* **Who holds them:**
  * heads and institution administrators: conduct;
  * inspectors: conduct (lesson observation);
  * HR officers and state officers: countersign.
* **Events:** `STF.APPRAISAL_SUBMITTED`, `STF.APPRAISAL_COUNTERSIGNED`, `STF.APPRAISAL_RETURNED`
  and `STF.LESSON_OBSERVED`.

## Consequences
* **Results feed later phases:**
  * promotion recommendations can inform posting requests;
  * training recommendations inform CPD (H4);
  * the lesson-planning criterion will draw on the lesson notes from lesson planning (W1) once built.
* **Expanded by ADR-026:** objectives (Performance Management System), self-assessment, probation
  reviews, appeals, CPD and development plans.
* **Open:**
  * per-subject observation forms;
  * printing the completed appraisal as a document.
