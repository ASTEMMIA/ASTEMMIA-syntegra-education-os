# ADR-027: Document and records management for schools (EDRMS)

**Status:** Accepted 2026-11-22. Design recorded in CEDA v1.11; built and recorded in CEDA v1.12
(package `syntegra.records`, migration 0017). The paper retention periods in the model file plans
are proposals to agree with the Abia State Government and the State Archives.
## Context
Abia's government schools hold large volumes of paper records. They must be digitised by scanning,
indexed for easy retrieval, registered to their school, and searchable, with access controlled by
sensitivity. Learner records are confidential.

The aim is also to free teachers and secretaries from filing and keeping track of paper. Paper files
that remain must be managed properly from creation to closure: classification, naming, file covers,
file lists, filing rules, tracking and disposal.

**What CEDA v1.0 already covers:**
* **Document 360:** `document`, `document_version` (append-only, SHA-256, S3) and `document_link`.
* **§6.3 EDRMS:**
  * digitisation projects, batches, jobs and quality checks;
  * physical custody: archives, locations, boxes, files and custody events;
  * document metadata (original, OCR, extracted and verified), and permissions.
* **The lifecycle:** draft → … → disposed, with legal holds.
* **Retention classes** (§16.4).
* **Document AI:** proposed metadata, validated by a person (§12).
* **Document numbers:** `DOC-<tenant>-<year>-<seq>`, printed as a QR code.

**What it does not cover:** the paper file plan, file numbering, file covers, file lists, circulation
tracking, closure, per-school registration and search, the capture workflow in detail, and the
security and backup commitments.

## Decision
### 1. Every document belongs to a school (or office), and is found by permission
* **Registered to its owner's scope:** each document and paper file is registered to the institution
  or office that owns it. A school sees its own records; the LGEA, ASUBEB and the state see everything
  beneath them.
* **Linked to what it is about:** a learner, staff member, guardian, institution or case (`document_link`).
  A learner's admission form is therefore found from the learner and from the school.
* **Classified by sensitivity**, which decides who may open it:

  | Classification | Examples | Who may open |
  |----------------|----------|--------------|
  | Public | prospectus, published policies | anyone signed in, in scope |
  | Internal | timetables, minutes, circulars | staff in scope |
  | Confidential | learner records, results, staff files | roles with the matching view permission (registrar, head, records officer, HR) |
  | Restricted | medical, SEND, disciplinary, HR casework | named roles only; every open is audited with a purpose |
  | Special | safeguarding | DSL and named officers only; break-glass with a reason (§8) |

* **Search results are filtered before they are returned.** A user never learns that a record they
  may not open exists. Opening Confidential or higher is logged; Restricted and Special also need a
  stated purpose.

### 2. Capture: scanning and indexing
* **Batches:** a digitisation project for a school is split into batches (one box or one file each).
  Each batch passes through preparation (staples removed, damage noted), scanning, quality check,
  indexing, verification and return of the originals.
* **Separator and cover sheets:** printed with a barcode or QR code, so a scanner splits a batch into
  documents and files each under the right paper-file reference automatically.
* **Scanning standard:** at least 300 dpi, colour where it carries meaning. The archival copy is
  PDF/A, OCR text is kept, and a SHA-256 of each original scan is stored.
* **Indexing:** document AI proposes the metadata (type, learner or staff name, dates, numbers). An
  indexer confirms or corrects it, and a second person checks a sample (§12: proposed, then
  validated by a person).
* **Metadata kept for each document:**
  * the document number, type, title, school, linked records, and dates (document date and filing date);
  * the paper-file reference and position in the file, the classification, the retention class;
  * the source (scanned, uploaded or created), and keywords;
  * the OCR text, and verification status and verifier.
* **Uploads and born-digital documents** follow the same register, with malware scanning on upload
  (§15).

### 3. Retrieval and accessibility
* **Search** works across metadata, OCR full text and keywords, filtered by school, type, person,
  date range and classification. Results are permission-filtered and ranked; pages are fast.
* **Viewing:** documents open in the browser; downloads and prints of Confidential or higher are
  logged and watermarked with the user and time.
* **Sharing:** time-limited links for outside parties (an examination body, a court) require a reason
  and expire. They are logged as disclosures (§8.4).
* **QR codes:** the document number on a printed copy leads to the record, for those permitted.

### 4. Versioning
* Every change of content is a new `document_version`; earlier versions are kept, with author, reason
  and hash.
* Metadata corrections are audited.
* **Final records are immutable,** for example signed results and certificates. A correction is a
  new version with a reason and approval, as for evidential records (§9).

### 5. Security, encryption and backup
* **Encryption:**
  * **at rest:** AES-256, using Amazon S3 server-side encryption with KMS keys, with a separate key
    for safeguarding (§8, §11);
  * **sensitive metadata** (names and identifiers in Restricted and Special records) is
    field-encrypted as Person 360 identifiers are (ADR-008);
  * **in transit:** TLS 1.2 or later.
* **Durability and backup**, three independent layers:
  1. S3 keeps each object across at least three Availability Zones in the residency region.
  2. S3 versioning and Object Lock (compliance mode for evidential records) protect against deletion
     and overwrites.
  3. Cross-region replication and AWS Backup copies go to the disaster-recovery region (§15).
* **Restore tests are run** and recorded.
* **Integrity:** the stored SHA-256 is checked on read and by periodic sweeps. A mismatch raises an
  incident.
* **Every action is audited** on the tenant's hash chain (§9): register, view, download, print, share,
  move, version, hold and dispose.

### 6. Managing paper files (those not yet scanned, and those kept as originals)
**Opening a file.** Every new file is registered before use. It gets a unique reference and a
meaningful title, without acronyms. A register entry and its printed **file cover** carry:

| File-cover field | Notes |
|------------------|-------|
| File reference | unique, from the file plan (below) |
| File title | meaningful and accurate |
| Date opened / date closed | |
| Owner | the person responsible for the contents |
| Department or team | e.g. Admissions, Bursary, Staff records |
| System reference | the matching record on the platform (learner number, staff number, case number) |
| Disposal date and action | from the retention schedule |
| Extra fields by file type | e.g. a staff file: appointment date and pension scheme date; a contract file: start date |

**Classification and numbering (the file plan).** Each school has a file plan of functions, then
activities, then files. References have two elements, and never more than three. Each series uses one
numbering scheme, applied consistently:

| Scheme | Example | Best for |
|--------|---------|----------|
| Single running number | `ADM/1`, `ADM/2` … | small series; an index is kept; not for very long runs |
| Annual single number | `FIN/2026/1` | financial and yearly records; makes annual closure systematic |
| Self-numbering | the invoice or receipt number itself | records that carry their own number |
| Alphabetical | `STAFF/OKEKE-ADA` | case files by name (staff, learners, buildings); no index needed |

**Filing rules,** shown on the cover and in guidance:
* file as soon as possible after receipt or creation;
* latest document on top, in order of the document's own date;
* enclosures directly beneath their covering document, and bulky items in a labelled pocket;
* no loose papers and no duplicates;
* mark the original and the copies; put a file note in other files saying where the original is.

When a scanned document came from a paper file, its record keeps the file reference and position, so
paper and digital stay matched.

**File lists.** Each file series has a list with: reference, title, dates opened and closed, system
reference, keywords and disposal details. Because the list lives on the platform, no separate manual
index is needed. When a file is destroyed, its entry is updated and kept.

**Tracking file movements** (circulation and charge-out):
* a file leaving the registry or office is booked out to a named person, with a due date and a
  purpose;
* overdue files are flagged, and returns are recorded;
* every movement is a custody event;
* files going outside the school (for example, to answer a data-protection request) are tracked too,
  and the platform suggests issuing copies rather than originals.

**Closing files.** A file is closed when it is complete (a learner leaves, a financial year ends) or
gets too thick, in which case part 2 is opened. Closure sets the disposal date from the retention class.

### 7. Retention, holds and disposal
* **Retention classes** are set per jurisdiction (§16.4), for example: learner record, admissions,
  results and certificates, finance (statutory years), staff files, safeguarding, minutes, and
  correspondence. The periods are to be agreed with the Abia State Government and the State Archives.
* **Disposal:** files and documents due for disposal are listed for a records officer, approved
  (four-eyes: `disposal:approve`) and recorded in a disposal certificate.
* **Legal holds always block disposal.**
* **Originals after scanning** are kept, returned or destroyed according to the class, only after
  the digital copy has passed quality check and its retention is set.

### 8. Roles
* **Records officer:** runs digitisation queues, quality checks, holds and disposal.
* **School secretary or registrar:** registers, files and books out files.
* **Indexer and verifier:** digitisation staff or a contractor.
* **Head teacher:** sees the school's records within the classification rules.
* **Records manager:** at ASUBEB or the state, owns the file plans, retention classes and disposal
  authority.

**Permissions:** `document:index` and `disposal:approve` (§21), plus the records permissions listed
under "What was built". New roles: ARCH (State Archives officer) and CAPT (capture operator).

## Decisions confirmed by the State (2026-11-22)
1. **Digital records are kept permanently.** Once a school record is digitised it stays in the
   state's education repository for good. There is no digital disposal: a document and every
   version of it can be reclassified or re-indexed, never deleted (the database refuses the delete).
   Retention periods and disposal apply **only to the paper originals**.
2. **The State Government and the State Archives control access to all of it.** Both hold roles at
   the state root (STA, and the new ARCH, State Archives officer), so they see every institution's
   records and the access log. Only State oversight (`records:oversight`) may lower a
   classification; schools may only raise one.
3. **Model file plans for primary, secondary and tertiary institutions** ship with the platform.
   An institution adopts the one for its levels (or names another) and may add local series.
4. **Scanning is done in-house or contracted out.** A batch names its operator kind and, for a
   contractor, the firm and contract reference. The new CAPT role scans only into batches assigned
   to that operator and cannot read or search the repository.

## What was built
**Tables** (`rec` schema, migration 0017): `records_series` (an institution's file plan),
`paper_file` (cover, home location, holder, legal hold, digitised flag), `file_movement`
(evidential), `digitisation_batch`, `document` (permanent), `document_version` (evidential),
`document_link`, `document_access` (evidential) and `paper_disposition`.

**Model file plans** (`records/file_plans/*.json`): primary 35 series, secondary 39, tertiary 32,
grouped by function (governance, learners or students, staff, teaching, finance, estates, plus
primary education, public examinations, student life, academic programmes and research). Each
series sets its numbering, default classification, paper retention and paper rule (destroy,
transfer to the State Archives, or retain), and whether it is a vital record.

**Numbering:** single running file `GOV/6` (then volumes), annual `FIN/2/2026/1`, personal (self)
`LRN/2/0001`, alphabetical `GOV/4/A/3`. The cover reference adds the institution code and volume.

**Capture:** open batch, operator captures (PDF, TIFF, JPEG or PNG, checked by its signature;
OCR text and proposed metadata may come from the capture station), finish, quality check by
someone other than the operator (sample size and defects), reject and rescan as new versions, or
accept. Each document gets a `DOC-<tenant>-<year>-<seq>` number and SHA-256.

**Indexing and verification:** a person confirms or corrects the metadata (or accepts the proposed
values), links the document to learners, staff, persons, institutions or applications, and files
it to a paper file and folio. A second person verifies it (not whoever captured or indexed it),
and only from an accepted batch. A file whose documents are all verified is marked digitised.

**Storage:** `BlobStore` port. Production uses S3 in af-south-1 with SSE-KMS (AES-256), a SHA-256
checksum S3 verifies, and `If-None-Match` so a stored file is never replaced; bucket versioning,
Object Lock, cross-region replication and AWS Backup are set in the infrastructure code. Dev/test
uses AES-256-GCM in memory or a folder; staging and production refuse to start with it. Downloads
are checked against the stored checksum.

**Access:** search (metadata and OCR text) and retrieval are trimmed by four new permissions,
`document:view_internal`, `_confidential`, `_restricted` and `_special`, at the caller's scope.
Restricted and Special records need a stated purpose. Every view and download is logged with its
purpose. Other new permissions: `document:capture`, `file:manage`, `records:admin`,
`records:oversight`.

**Paper files:** open, issue, pass, send out to another office, return (with due dates and an
overdue list), close, new volume, legal hold, and the file list as CSV. Paper disposition is
proposed by a records administrator and decided by another person with `disposal:approve`, with a
destruction certificate or State Archives transfer reference. Destruction needs the file to be
digitised first; legal holds block it; series marked "retain" are never destroyed.

**Endpoints** (`/v1`): model file plans; an institution's file plan (adopt, add series); files,
files.csv, movements, close, new volume, legal hold, digitised; batches, my batches, capture,
finish, check; documents (upload, search, read, content, index, verify, versions, classification,
access log); disposition due, request, decision, list; and the repository overview.

## Consequences
* **Builds on Document 360 and §6.3** rather than a separate product. Every document is linked to the
  learner, staff or institution records already on the platform (ADR-012, ADR-024).
* **Build order:**
  1. the register, file plans and file covers;
  2. capture and indexing, with quality check;
  3. search and access control;
  4. circulation tracking;
  5. retention and disposal.

  Digitisation billing (§6.3) follows if the scanning is contracted out.
* **Open:**
  * agree the paper retention periods in the three model plans with the State Archives;
  * production OCR (Textract) and search ranking (OpenSearch); the same permission trimming applies;
  * the S3 bucket, KMS key, Object Lock and replication in the infrastructure code;
  * the scanning hardware, and digitisation billing (§6.3) for contracted batches.
