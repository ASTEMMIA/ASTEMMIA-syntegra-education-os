# ADR-031: Pilot institutions, demonstration data and the Government Monitoring Dashboard

**Status:** Accepted 2026-11-22. Built (packages `syntegra.pilot` and `syntegra.monitoring`, the
`pilot-load` and `pilot-demo` commands, the Monitoring screen). Phase 1 of the ASUBEB pilot for the
Executive Council. Open: the remaining LGA and postcode confirmations, the coat of arms, the
hosting target, and the modules the dashboard lists as not recorded yet.

## Context
ASUBEB asked for seven pilot institutions, representing different categories, to be run as fully
digital schools for the State Executive Council. It asked for 2,500 learner names, the State's
coat of arms, and a Government Monitoring Dashboard. The dashboard should show:

* schools onboarded;
* learner and teacher populations;
* attendance and academic performance;
* infrastructure;
* inspection reports;
* where the schools are.

It asked that the list of institutions be **treated as confidential** until formally approved.

Phase 1 registers the institutions, loads the learners as demonstration data, and builds the
first version of the dashboard.

## Decision

### The pilot list stays out of the repository
* The operator supplies it at deployment as `deploy/pilot/pilot-institutions.csv`. It uses the
  institution template plus a `board` column naming the registering body. Git ignores every CSV
  in that folder except a fictional example. Neither this ADR nor the CEDA names the institutions.
* `python -m syntegra.cli pilot-load <file>` registers them through the ordinary institution
  import. Every row of every body is checked first, and one problem stops the whole load, so
  nothing is half-registered. A second run finds the institutions already registered instead of
  duplicating them.
* **Which body registers what** follows the Abia structure file:
  * ASUBEB registers primary and secondary schools;
  * the Ministry of Tertiary Education registers the technical (TVET) school and the polytechnic.

### LGA corrections
* One pilot primary school in Aba was listed under **"Aba Central LGA"**, which does not exist:
  Aba has Aba North and Aba South. The State confirmed **Aba North**. The import refuses
  "Aba Central" (a test proves it), so the mistake cannot reach the register.
* Where the structure file's LGA name differs from common usage, the pilot file uses the
  structure file's name (for example "Osisioma", also known as Osisioma Ngwa).
* The other LGAs, three postcodes and every coordinate are **to be confirmed by the State**. They
  are listed with the pilot file at deployment, not here, because naming the places would identify
  the schools. The coordinates place each school at its town, not a surveyed site.

### Demonstration data
* `pilot-demo <file> --learners 2500` gives every pilot institution:
  * made-up learners, each with a guardian;
  * classes, as arms of up to 40, each with a made-up class teacher;
  * at the polytechnic: learners enrolled in their year (they take courses, not classes), with one
    made-up lecturer for every 25 learners.
* Learners are shared out in proportion to the class years each institution runs. A girls' school
  gets only girls. Names are common Nigerian (mostly Igbo) names combined at random from a fixed
  seed, so the same run gives the same people.
* **Marked at the source:** every learner, guardian and teacher made this way has
  `source_system = "demo"` on their Person 360 record. From that mark:
  * Learner 360 shows a banner, and class lists and search show a "Demonstration data" badge;
  * the dashboard counts demonstration learners and teachers separately and says so in a banner.
* **Never mixed with real people:** Person 360 matches demonstration records only with each other,
  so a real child with the same name and birthday as a made-up one is registered as a new person.
  Guardian phones use the 0700 business range, never a mobile number.
* **Never in production:** the command refuses to run when `SYNTEGRA_ENV=prod`, and it refuses to
  run twice in the same state.

### Government Monitoring Dashboard (`monitoring:view`)
Who may view it: state, federal, inspectorate, head and institution-administrator roles. An
office sees every institution beneath it; a head sees their own institution. It shows counts only,
never names or message text, and refreshes every minute.

* **Built from the platform's records:**
  * institutions onboarded, by type;
  * learners enrolled, girls and boys, by level;
  * teachers in post, and learners per teacher;
  * classes and places filled;
  * admissions waiting and past their 10-day decision date;
  * transfers waiting;
  * records digitised;
  * messages in the last 7 days;
  * a table by LGA and by institution;
  * where the institutions are, plotted from their recorded coordinates (no map tiles: the app
    loads nothing from other sites).
* **Listed as not recorded yet, never estimated:** attendance, academic performance,
  infrastructure (broadband, Wi-Fi, solar classrooms, devices) and inspection reports. Each one
  appears once its module records it.

### Coat of arms
The State's emblem in the app is the file supplied on 22 November 2026. Whether it is the
officially adopted version is **not confirmed**. Replacing it is a one-file change
(`web/public/brand/ng-ab.webp`) once the Governor's office or the Ministry of Information supplies
the official artwork.

## Consequences
* The Executive Council can see seven working institutions with 2,500 learners. Every made-up
  record says what it is, and the confidential list never enters the repository's history.
* Removing the demonstration data before go-live needs a decision. The records reach into
  enrolments, classes, staff posts and the audit chain, and there is no tool to remove them in
  place. The expected route is a fresh production database without them, which the production
  refusal already enforces.
* Each dashboard area marked "not recorded yet" is a module still to build. The infrastructure
  showcase (broadband, Wi-Fi, solar, backup, cybersecurity) is shown only once it is installed and
  recorded.
