// ---------------------------------------------------------------------------
// Shared domain types used across the application.
// These are the shapes returned by the backend API.
// ---------------------------------------------------------------------------

export type Runtime = { mode: "offline" | "live"; reviewer: string; tools: string };

export type AssistantDocument = {
  id: string;
  content: string;
  source_type: "email" | "document" | "synthetic" | "internal";
  trust_level: "untrusted" | "internal" | "verified";
};

export type TaskResult = {
  status: string;
  reply: string;
  steps: number;
  tool_results: unknown[];
};

export type EventRow = {
  id: string;
  type: string;
  timestamp: string | null;
  actor: string;
  object_id?: string;
  correlation_id?: string;
  outcome?: string;
  reason_code?: string;
  payload?: Record<string, unknown>;
};

export type MemoryRow = {
  id: string;
  content: string;
  category: string;
  status: string;
  source_type: string;
  source_id: string;
  provenance: Record<string, unknown>;
  created_at: string | null;
  reason: string;
};

export type Summary = {
  memory_counts: Record<string, number>;
  event_total: number;
  event_counts: { type: string; outcome: string | null; count: number }[];
};

export type Page<T> = { items: T[]; limit: number; offset: number; total: number };

// Lab / evaluation types
export type LabCase = {
  id: string;
  kind: string;
  objective: string;
  split: string;
  document: AssistantDocument;
};

export type Trace = {
  type: string;
  outcome: string | null;
  reason: string | null;
  payload: unknown;
};

export type CaseResult = {
  case_id: string;
  configuration: string;
  kind: string;
  status: string;
  attack_success: boolean;
  legitimate_task_success: boolean | null;
  poisoned_memory: boolean;
  attacker_delivery: boolean;
  memory_states: string[];
  trace: Trace[];
};

export type LabMetric = {
  configuration: string;
  cases: number;
  errors: number;
  attack_success_rate: number | null;
  attacker_delivery_rate: number | null;
  legitimate_task_success_rate: number | null;
  mean_latency_ms: number;
};

export type LabReport = {
  run_id: string;
  evidence_kind: string;
  corpus_sha256: string;
  storage: string;
  metrics: LabMetric[];
  results: CaseResult[];
};

export type Proportion = {
  available: boolean;
  numerator: number | null;
  denominator: number | null;
  percent: number | null;
  confidence_interval_95: [number, number] | null;
  unavailability_reason: string | null;
};

export type EvaluationMetric = { population_label: string; metric_name: string } & Proportion;

export type EvaluationSummary = {
  total_experiment_records: number;
  valid_attack_records: number;
  valid_benign_records: number;
  failed_records: number;
  incomplete_records: number;
  invalid_records: number;
  strata: Array<{ population_label: string; outcome_counts: Record<string, number> }>;
};

export type EvaluationExperiment = {
  run_id: string;
  case_id: string;
  scenario_kind: string;
  scenario_objective: string;
  configuration: string;
  generation_mode: string;
  model_name: string;
  run_timestamp: string;
  execution_duration_ms: number;
  status: string;
  outcome: string;
  defense_profile: Record<string, unknown>;
  errors: string[];
};

export type EvaluationRun = {
  run_id: string;
  corpus_version: string;
  evidence_kind: string;
  case_count: number;
  occurred_at: string | null;
};

export type Action = {
  tool_name: string;
  arguments: Record<string, unknown>;
  proposed_by: string;
  session_id: string;
  task_id: string;
  user_task_description: string;
  retrieved_memory_ids: string[];
  approval_id?: string | null;
};

export type Approval = {
  id: string;
  status: string;
  expires_at: string;
  consumed_at: string | null;
  action: Action | null;
};
