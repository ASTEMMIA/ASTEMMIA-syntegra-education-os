# ADR-032: Attendance, continuous assessment and examinations

**Status:** Accepted 2026-11-22. Built: packages `syntegra.attendance` and `syntegra.assessment`,
migration 0020, and screens for attendance, staff attendance, markbook, results, examination
timetable, report cards, transcripts and parents' "My children".

**Open:**
* the State to confirm the default grading rules (below);
* the NCE classes of award;
* lesson-by-lesson (subject) registers;
* SMS alerts to parents for absence;
* computer-based tests (part of the LMS);
* exam-centre registration with the examination bodies.

## Context
The pilot needs attendance for learners and staff, and examination management: continuous
assessment, scheduling, results, report cards and transcripts. The Government Monitoring Dashboard
(ADR-031) listed attendance and academic performance as "not recorded yet". CEDA §5 names the
entities (attendance session and mark, assessment, grade, report).

## Decision

### Attendance (schema `att`)
* **School days.** A school day is a weekday inside a term of the class's session, unless a
  closure applies:
  * a closure set by the State or a board (a public holiday) covers every school beneath it;
  * a school can close itself.
* **Class registers, twice a day.** Each class has a morning and an afternoon register, and every
  learner in the class that day is marked:
  * **present** or **late** (both count as present);
  * **absent**;
  * **excused**, which needs a reason.
* **Who takes registers, and when.** The class teacher (or assistant) takes them.
  * A missed register can be taken up to **7 days** late.
  * Changing a mark after the day needs `attendance:amend` (heads, institution administrators)
    and a reason.
  * Each correction is kept as an evidential record.
* **Figures.**
  * *Times the school opened* is the number of registers taken while the learner was in the
    class; *present* is the learner's present and late marks; the *rate* is present divided by
    times opened.
  * **Persistent absence** means a rate below 90% over at least 10 registers.
  * These figures feed the report card and the dashboard.
* **Staff.** Staff sign themselves in and out, or the head records the day (`staff_attendance:record`).
  * The statuses are present, late, absent, leave, sick and official duty.
  * Signing in after 08:00 counts as late. The time is configurable as
    `attendance.staff_late_after`.
  * Times are West Africa Time.

### Assessment rules (schema `acad`)
Four groups of levels have defaults. The State, a board or a school can set its own rules for a
group (`assessment:configure`); its parts must add up to 100.

| Group  | Levels                          | Parts                         | Grading                    |
|--------|---------------------------------|-------------------------------|----------------------------|
| basic  | ECCDE, primary, JSS, TVET, adult | 1st test 20, 2nd test 20, exam 60 | A 70, B 60, C 50, D 45, E 40, F; pass 40 |
| senior | SSS                             | 1st test 20, 2nd test 20, exam 60 | WAEC A1 75 … F9; credit 50 |
| nbte   | ND, HND                         | CA 30, exam 70                | NBTE AA 75 (4.00) … F; Distinction 3.50, Upper credit 3.00, Lower credit 2.50, Pass 2.00 |
| nuc    | NCE, degree, postgraduate       | CA 30, exam 70                | A 70 (5.00) … F; First 4.50, 2:1 3.50, 2:2 2.40, Third 1.50, Pass 1.00 |

A result sheet keeps the rules it was opened under, so a later change does not regrade results
already entered.

### Result sheets: one per subject (or course) per term
* **Scores.** The subject teacher enters each learner's score for every part, or marks them
  absent (`markbook:edit`; teachers only for subjects they are assigned to).
  * A school subject is assessed each term. A tertiary course is assessed in its own semester.
  * Who takes it: the class's members for core subjects; registered learners for electives and
    courses.
* **Submitting** needs every learner complete.
* **The head approves or returns.** The head (`results:approve`) approves a sheet or returns it
  with a note.
  * Nobody approves results they submitted themselves (four eyes).
  * An approved sheet is locked. Reopening it needs a reason.
  * Each step (submitted, approved, returned, reopened) is an evidential event.
* **Totals and positions.** The total is the sum of the parts, with an absence counting as 0.
  * The grade comes from the sheet's own rules.
  * Positions share ties (1, 1, 3).
  * The sheet shows the class average, highest, lowest and how many passed.

### Examination timetable (`exam:schedule`)
* Each paper sits inside its term and lasts 10 minutes to 6 hours.
* A paper is refused if it clashes with:
  * another paper taken by any of the same learners (a class, or tertiary students registered
    for both courses);
  * a paper in the same room;
  * a paper with the same invigilator.

### Report cards (schools)
* **When.** Once every subject a class takes is approved for the term, the head publishes the
  class's report cards.
* **What each card shows:**
  * for each subject: the parts, total, grade and remark, position, class average, and highest and
    lowest marks;
  * overall: total, average, and position out of the class;
  * attendance for the term;
  * the class teacher's and the head's remarks;
  * the date the next term begins.
* **Kept as issued.** Each card is stored exactly as issued, with a checksum. A correction
  (reopen, re-approve, republish) makes a **new version**. An unchanged card is not issued again.
* **Late joiners.** A learner who joined after a subject's results were approved has no result in
  it until the sheet is reopened and they are marked.

### Tertiary results and transcripts
* **Course results.** An approved course result is released.
  * GPA per semester and CGPA are weighted by course units.
  * The class of award (for example Distinction, Second class upper) comes from the group's
    rules.
* **Transcripts** (`transcript:issue`) gather a learner's published report cards (schools) or
  approved courses (tertiary).
  * Each is issued for a stated purpose, with a reference such as `TRN-2026-000001` and a
    checksum.
  * It is kept exactly as issued.

### Parents
* A parent sees only their own children: where they hold parental responsibility and no court
  order applies.
* For each child they see attendance this term and the published report cards, under
  "My children" (now a parent's home page).

### Government Monitoring Dashboard
The dashboard now shows:
* learner attendance and staff attendance over the last 30 days;
* morning registers taken today, out of the number of classes;
* results approved, the average score, and the pass rate (from each sheet's own pass mark);
* each of these per institution.

"Not recorded yet" now lists only infrastructure and inspections.

### Demonstration activity (ADR-031)
`pilot-activity --days N` adds the following, for demonstration learners and teachers only:
* registers and staff days for the last *N* school days;
* the term's first two tests.

It does not add examination results or report cards: the term's examinations have not happened
yet.

## Consequences
* **What is now real on the dashboard:**
  * the State can see whether registers are taken and how many children attend;
  * results come from approved sheets only, never drafts.
* **A record that holds up.** Every correction, to a register, a result or a report card, leaves
  a dated record with a reason, so a disputed result can be traced.
* **The defaults need the State's confirmation.** Changing them later does not regrade sheets
  already opened.
* **Not built yet:**
  * subject (lesson) registers;
  * SMS absence alerts;
  * computer-based tests;
  * end-of-session promotion decisions driven by results. Promotion exists, but it is decided by
    the head.
