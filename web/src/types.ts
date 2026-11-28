/** Shapes the records API returns (ADR-027). Ids and dates arrive as strings. */

export type Classification = "public" | "internal" | "confidential" | "restricted" | "special";
export const CLASSIFICATIONS: Classification[] = ["public", "internal", "confidential", "restricted", "special"];

export const DOC_TYPES = [
  "admission_form", "report_card", "result_sheet", "certificate", "transcript", "letter",
  "circular", "minutes", "policy", "report", "form", "receipt", "voucher", "contract", "plan",
  "photograph", "other",
] as const;
export type DocType = (typeof DOC_TYPES)[number];

export interface Grant {
  grant_id: string;
  role: string;
  role_name: string | null;
  scope_node_id: string;
  scope_name: string | null;
}

export interface Me {
  user_id: string;
  display_name: string | null;
  tenant_id: string;
  tenant_code: string | null;
  active_scope_id: string;
  grants: Grant[];
  permissions_here: Record<string, string>;
}

export interface Institution {
  id: string;
  name: string;
  institution_code: string;
  government_code: string | null;
  lga: string;
  levels: string[]; // labels: "Primary", "JSS"
  level_codes: string[]; // codes: "primary", "jss"
  scope_node_id: string;
}

export interface Series {
  id: string;
  plan: string;
  function_code: string;
  function_name: string;
  code: string;
  title: string;
  activity: string | null;
  numbering: "single" | "annual" | "self" | "alphabetical";
  classification: Classification;
  paper_retain_years: number | null;
  paper_rule: "destroy" | "transfer_archives" | "retain";
  vital: boolean;
  active: boolean;
}

export interface PaperFile {
  id: string;
  institution_id: string;
  institution_name: string;
  series_id: string;
  series_code: string;
  series_title: string;
  file_number: string;
  reference: string;
  title: string;
  person_id: string | null;
  classification: Classification;
  volume: number;
  previous_file_id: string | null;
  opened_on: string;
  closed_on: string | null;
  status: "open" | "closed" | "disposed" | "transferred";
  home_location: string;
  holder_user_id: string | null;
  holder_name: string | null;
  out: boolean;
  due_back: string | null;
  overdue: boolean;
  digitised: boolean;
  legal_hold: boolean;
  keywords: string[];
  paper_rule: Series["paper_rule"];
  paper_due: string | null;
}

export interface Movement {
  id: string;
  kind: "issue" | "pass" | "external_out" | "return";
  to_user_id: string | null;
  to_name: string | null;
  moved_at: string;
  due_back: string | null;
  note: string | null;
  by: string | null;
}

export interface Batch {
  id: string;
  batch_number: string;
  institution_id: string;
  operator_kind: "in_house" | "contractor";
  contractor_name: string | null;
  contract_reference: string | null;
  operator_user_id: string;
  description: string;
  status: "open" | "captured" | "accepted" | "rejected";
  documents: number;
  pages: number;
  qc_by: string | null;
  qc_sampled: number | null;
  qc_defects: number | null;
  qc_note: string | null;
}

export interface Doc {
  id: string;
  document_number: string;
  institution_id: string;
  title: string;
  doc_type: DocType;
  classification: Classification;
  status: "captured" | "indexed" | "verified";
  source: string;
  document_date: string | null;
  series_id: string | null;
  file_id: string | null;
  folio: number | null;
  batch_id: string | null;
  keywords: string[];
  current_version: number;
  page_count: number | null;
}

export interface Version {
  version: number;
  sha256: string;
  size_bytes: number;
  mime: string;
  original_name: string | null;
  note: string | null;
  created_at: string;
  created_by: string | null;
}

export interface DocDetail {
  document: Doc;
  institution_name: string;
  file_reference: string | null;
  versions: Version[];
  links: { kind: string; target_id: string }[];
  proposed: Record<string, unknown> | null;
  captured_by: string | null;
  indexed_by: string | null;
  verified_by: string | null;
  verified_at: string | null;
}

export interface Access {
  user_id: string | null;
  action: string;
  version: number | null;
  purpose: string | null;
  at: string;
}

export interface Disposition {
  id: string;
  file_id: string;
  file_reference: string;
  action: "destroy" | "transfer_archives" | "retain";
  reason: string;
  status: "pending" | "approved" | "rejected";
  requested_by: string | null;
  decided_by: string | null;
  decision_note: string | null;
  certificate_reference: string | null;
}

export interface RepoRow {
  institution_id: string;
  institution_name: string;
  series: number;
  files_open: number;
  files_closed: number;
  files_out: number;
  files_overdue: number;
  files_digitised: number;
  documents: number;
  documents_verified: number;
  pages: number;
  by_classification: Partial<Record<Classification, number>>;
  batches_waiting_check: number;
}

export interface Items<T> {
  items: T[];
}

// --------------------------------------------------------------------------- classes and learners (ADR-029)

export interface Period {
  id: string;
  kind: "session" | "term" | "semester";
  name: string;
  starts_on: string;
  ends_on: string;
  status: "planned" | "current" | "closed";
  set_at: string;
  parts: Period[];
}

export interface Room {
  id: string;
  code: string;
  name: string;
  kind: string;
  capacity: number;
  building: string | null;
}

export interface SubjectRef {
  id: string;
  code: string;
  name: string;
  group: string | null;
  institution_id: string | null;
  units: number | null;
  year_code: string | null;
  semester: number | null;
  department: string | null;
}

export interface Assignment {
  id: string;
  staff_id: string;
  staff_name: string | null;
  role: "lead" | "assistant";
  valid_from: string;
  valid_to: string | null;
  note: string | null;
}

export interface OfferingRef {
  id: string;
  period_id: string;
  class_group_id: string | null;
  year_code: string;
  subject: SubjectRef;
  kind: "core" | "elective" | "optional";
  periods_per_week: number | null;
  units: number | null;
  capacity: number | null;
  active: boolean;
  teachers: Assignment[];
}

export interface ClassRef {
  id: string;
  institution_id: string;
  session_id: string;
  session_name: string;
  year_code: string;
  year_name: string;
  level: string;
  arm: string;
  name: string;
  capacity: number;
  room: Room | null;
  class_teacher_staff_id: string | null;
  class_teacher_name: string | null;
  assistant_staff_id: string | null;
  status: "active" | "closed";
  size: number;
  places_left: number;
  subjects?: OfferingRef[];
  warnings?: string[];
}

export interface ClassYear {
  code: string;
  name: string;
  level: string;
  order: number;
  typical_age: number | null;
}

export interface Member {
  demo?: boolean; // demonstration data (ADR-031)
  enrolment_id: string;
  learner_id: string;
  learner_number: string;
  surname: string;
  first_name: string;
  other_names: string | null;
  sex: string;
  date_of_birth: string | null;
  admission_number: string | null;
  since: string;
}

export interface EnrolmentRef {
  demo?: boolean; // demonstration data (ADR-031)
  id: string;
  learner_id: string;
  learner_number: string;
  learner_name: string;
  institution_id: string;
  institution_name: string;
  session_id: string;
  session_name: string;
  year_code: string;
  year_name: string;
  class_id: string | null;
  class_name: string | null;
  status: string;
  source: string;
  admission_number: string | null;
  valid_from: string;
  valid_to: string | null;
  exit_reason: string | null;
  age_override: string | null;
}

export interface LearnerRef {
  demo?: boolean; // demonstration data (ADR-031)
  id: string;
  learner_number: string;
  person_id: string;
  surname: string;
  first_name: string;
  other_names: string | null;
  sex: string;
  date_of_birth: string | null;
  status: string;
  first_enrolled_on: string | null;
  current: EnrolmentRef | null;
  history: EnrolmentRef[];
  guardians: { person_id: string; name: string; relationship: string; parental_responsibility: boolean; contact_priority: number; phone: string | null; court_order_flag: boolean; has_account?: boolean }[];
}

export interface Application {
  id: string;
  reference: string;
  session_id: string;
  year_code: string;
  year_name: string;
  status: string;
  channel: "school" | "online" | "transfer";
  applicant_name: string;
  date_of_birth: string;
  sex: string;
  previous_school: string | null;
  guardian_name: string;
  guardian_phone: string;
  guardian_relationship: string;
  assessment_score: string | null;
  assessment_note: string | null;
  decision_note: string | null;
  offer_expires_on: string | null;
  due_by: string;
  overdue: boolean;
  age_note: string | null;
  enrolment_id: string | null;
  history: { at: string; status: string; by: string | null; note: string | null }[];
}

export interface Transfer {
  id: string;
  learner_id: string;
  learner_number: string;
  learner_name: string;
  from_institution: string;
  to_institution: string;
  to_institution_id: string;
  year_code: string;
  effective_on: string;
  reason: string;
  status: string;
  decision_note: string | null;
}

export interface Curriculum {
  version_id: string;
  code: string;
  name: string;
  authority: string;
  version: number;
  status: string;
  note: string | null;
  adoptions: { scope_id: string; scope_name: string; years: string; from_session: string }[];
}

export interface StaffRef {
  id: string;
  staff_number: string;
  surname: string;
  first_name: string;
  category: string;
  registered_teacher: boolean;
  current_post: { institution_id: string | null; position: string } | null;
}
