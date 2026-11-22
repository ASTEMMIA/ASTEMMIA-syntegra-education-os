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
  levels: string[];
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
