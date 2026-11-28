# ADR-029: Learner registration, classes, curriculum and teaching staff

**Status:** Accepted 2026-11-22. Built (packages `syntegra.academic` and `syntegra.learners`,
migration 0018, screens in the web app). The model curriculum is to be confirmed by the State.

## Context
The platform knew institutions, staff (ADR-024) and records (ADR-027), but not learners, classes
or what is taught. The CEDA defines Learner 360 (§4), admissions and enrolment, academic structure
(academic period, class group, class membership), Curriculum 360 (curriculum, immutable versions,
adoption), the admission and onboarding workflows (§17) and record-level validation such as the
age band (§16.2).

The user asked for the registration process, the classroom process, classes and teachers, and
assigning curriculum and teaching staff, and chose:

* **Registration** by all four routes: admission application, direct entry by the school, bulk
  import of class lists, and transfer from another school.
* **Classes** for both models: schools run a session of three terms with arms (Primary 4A, JSS 1
  Gold); tertiary institutions run a session of two semesters with courses and lecturers.
* **Curriculum:** no preference, so the recommended option: the national subject list is loaded as
  a curriculum the State adopts; tertiary institutions keep their own courses.

## Decision

### Academic structure (`syntegra.academic`)
* **Calendar.** A session (`2026/2027`) with three terms (schools) or two semesters (tertiary).
  The board (ASUBEB) sets the school calendar once for every school beneath it; a tertiary
  institution sets its own. Periods are visible from their scope, above and below it; only the
  office that set a calendar changes it (`calendar:manage`).
* **Class years.** Nursery 1-2 and Kindergarten; Primary 1-6; JSS 1-3; SSS 1-3; adult basic
  literacy, post-literacy and continuing education; TVET 1-3; NCE 1-3; ND 1-2; HND 1-2;
  100-600 Level; PGD, Master's, Doctorate. Typical ages give the age band for schools.
* **Curriculum.** A model list of 42 subjects in 195 year-subject entries, arranged as the NERDC
  Basic Education Curriculum (lower, middle and upper basic) and the Senior Secondary curriculum
  (English, Mathematics and Civic Education as core; electives including a trade or
  entrepreneurship subject), with early-years and adult-literacy subjects. It is loaded as a
  published, immutable version (`syntegra seed-curriculum --tenant NG-AB`) and **adopted** by the
  State (or a board) for some years from a session (`curriculum:publish`). A class resolves to the
  nearest adoption above its school, so each class has exactly one version per subject (CEDA §4).
* **Classes.** One arm of one year in one session, with a capacity (1-120), an optional room
  (which must seat the capacity) and a class teacher. Opening a class lays out its subjects from
  the adopted curriculum: core, elective and optional, with periods a week. Schools may add
  their own subjects; codes are unique against the State's list.
* **Teaching staff.** Each offering (a subject in a class, or a course in a semester) has a lead
  teacher and optional assistants. Only **active teaching staff posted to the institution** may
  be class teachers or teach there. A new lead replaces the current one from the start date; an
  assistant may be made lead. A weekly load above the limit (default 36 periods, setting
  `academic.max_periods_per_week`) is refused. Missing TRCN registration or a missing verified
  teaching qualification is **allowed with a warning**, which is kept on the assignment.
* **Tertiary.** An institution's own courses (code, title, units, level, semester, department)
  run in a semester as offerings, with lecturers assigned the same way.

### Learner registration (`syntegra.learners`)
* **Learner 360.** One learner record per person for life, with an `LRN-NNNN-NNNN-CC` number
  (MOD 97 check digits). Its scope follows the current enrolment, so the school a learner attends
  sees them and the bodies above see them all. Guardians are people linked with relationship,
  parental responsibility, contact priority and a court-order flag that withholds contact details.
* **Four routes in, one way of enrolling.** Every route ends in the same steps: match or create the
  person (Person 360 duplicate checks), find or create the learner, link the guardian, open the
  enrolment, check the age band, place in a class within its capacity.
  1. **Admission application:** submitted at the school or online (public form with the CAPTCHA,
     ADR-015), reviewed, optionally assessed (score 0-100), then offered, waitlisted or rejected
     (`admission:decide`, a head teacher), accepted or declined by the family, and enrolled. A
     decision is due in 10 working days; overdue applications are flagged. An offer stays open
     14 days by default. A rejection needs a reason the family is told. The applicant becomes a
     person and learner only at enrolment, so applications that go nowhere leave no master data.
  2. **Direct entry** by the registrar, for a pupil already sitting in a class.
  3. **Class-list import:** the school's existing register as CSV, previewed row by row (names,
     sex, date of birth, class, capacity, duplicates within the file and on the platform), then
     committed. An existing pupil outside the age band is recorded, not refused.
  4. **Transfer:** the sending school asks; the receiving school accepts and places the learner (or
     declines with a reason). The old enrolment ends as `transferred_out`, the new one starts as
     `transfer`, and the learner moves into the receiving school's scope.
* **One active enrolment per learner**, enforced by a unique `active_key` (CEDA §6).
* **Duplicates.** A child's own record has no phone, so Person 360 scores a re-registration on name
  and date of birth only (review band). For learners the guardian's phone decides: the same name,
  date of birth and guardian phone **is the same child** and registration is blocked unless the
  registrar confirms they are different, with a reason. Siblings share one record for their parent
  (same phone and surname).
* **Age band (CEDA §16.2):** a learner more than one year younger or three years older than the
  usual age for the year is enrolled only with a stated reason.
* **Promotion** at the end of a session moves a class into next year's class in a later session;
  named repeaters go into the same year's class. Old enrolments complete; new ones are linked.
* **Subjects.** Senior secondary students take eight or nine subjects including the core ones and
  a trade or entrepreneurship subject (WAEC/NECO); tertiary students register 15-24 units a
  semester, within course capacity.
* **Teachers' access.** `learner:view` with the "own classes" condition (TCH) shows only the lists
  of classes the teacher is class teacher of or teaches, and those learners' records.

### Roles and permissions
New: `learner:register` (REG, IADM, HEAD), `admission:decide` (HEAD; IADM partial),
`class:manage` (HEAD, IADM), `calendar:manage` (STA; IADM for its own semesters),
`teaching:assign` (HEAD, IADM). Existing: `learner:view`, `curriculum:publish` (STA, CURA).

### Events
`ADM.SUBMITTED`, `ADM.DECIDED`, `ENR.STARTED`, `ENR.CHANGED`, `ENR.ENDED`, `TRF.REQUESTED`,
`TRF.DECIDED`. Payloads carry ids and codes only.

### Screens (ADR-028)
Learners (find, enrol directly, Learner 360, move class, transfer, leave), Admissions
(applications and their history, decisions, enrolment; transfers in and out), Classes (classes by
session with fill against capacity, open a class, rooms, class page with class teacher, class
list, subjects and teachers, promotion), My classes (teachers), Calendar and curriculum, and the
class-list import. A browser walk-through covers each role.

## Consequences
* Migration 0018 adds 17 tables in `acad`. The institutions API adds `level_codes` beside the
  `levels` labels.
* **Open:**
  * the State to confirm the model curriculum (or load the current official NERDC version) before
    adopting it, and the age bands, offer period and decision deadline;
  * the parent and student portal (guardian sign-in), timetables and rooms per period, attendance,
    assessment, fees and transport from the onboarding workflow;
  * a learner's records (ADR-027 documents) following them on transfer;
  * JAMB/UTME admission checks for tertiary entry.
