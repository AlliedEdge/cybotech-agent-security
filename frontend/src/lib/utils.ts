import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function percent(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

/** Translate internal event type strings to plain language. */
export const EVENT_LABELS: Record<string, string> = {
  document_ingested:          "Document uploaded",
  document_retrieval:         "Document information retrieved",
  document_deleted:           "Document removed",
  retrieval_completed:        "Memory search completed",
  memory_guard_decision:      "Memory reviewed",
  memory_proposal_invalid:    "Memory proposal rejected",
  memory_lifecycle_transition:"Memory status updated",
  action_proposed:            "Action proposed by assistant",
  sentinel_finding:           "Security concern identified",
  gateway_decision:           "Action decision recorded",
  gateway_authorized:         "Action approved",
  gateway_denied:             "Unsafe action blocked",
  approval_requested:         "Approval requested",
  approval_decision:          "Approval decision recorded",
  tool_execution_completed:   "Simulated action executed",
  task_failed:                "Task ended with error",
  lab_run_completed:          "Experiment completed",
  lab_case_completed:         "Experiment case completed",
};

export function labelEvent(type: string): string {
  return EVENT_LABELS[type] ?? type;
}
