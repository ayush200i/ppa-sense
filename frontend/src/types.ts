export interface ContractDocument {
  doc_id: string;
  filename: string;
  size_bytes: number;
  status: "queued" | "processing" | "ready" | "failed";
  created_at: string;
  chunks_stored: number;
  warnings: string[];
  error: string | null;
}

export interface Citation {
  doc_id: string;
  page: number;
  section: string;
  quote: string;
}

export const fieldLabels = {
  contract_type: "Contract type",
  parties: "Parties",
  effective_date: "Effective date",
  term_years: "Term (years)",
  COD: "Commercial operation date",
  price_tariff: "Price / tariff",
  escalation: "Escalation",
  delivery_point: "Delivery point",
  guaranteed_output: "Guaranteed output",
  termination_rights: "Termination rights",
  liquidated_damages: "Liquidated damages",
  change_in_law: "Change in law",
  force_majeure: "Force majeure",
  payment_terms: "Payment terms",
} as const;

export type FieldName = keyof typeof fieldLabels;
type Value = string | number | null | { [key: string]: Value } | Value[];

export interface SourcedField {
  value: Value;
  sources: Citation[];
  confidence: number;
  status:
    "validated" | "low_confidence" | "not_found" | "invalid_evidence" | "error";
  reason: string;
  attempts: number;
}

export interface Extraction {
  doc_id: string;
  terms: Record<FieldName, SourcedField>;
  needs_review: FieldName[];
  warnings: string[];
}

export interface Answer {
  answer: string;
  citations: Citation[];
}
export interface DocumentList {
  documents: ContractDocument[];
  limit: number;
  offset: number;
}
