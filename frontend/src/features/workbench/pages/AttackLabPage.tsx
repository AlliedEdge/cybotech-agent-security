/**
 * AttackLabPage — run synthetic adversarial experiments against the assistant.
 *
 * Design:
 *  - Scripted mode is the default; live-model is an explicit opt-in
 *  - Shows current attacker provider/model when in live mode
 *  - Plain-language result descriptions alongside technical data
 *  - Detailed trace evidence expandable per case
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Play, Download, ChevronDown, ChevronUp, FlaskConical } from "lucide-react";
import { api } from "@/api";
import { Panel, Button, Select, Badge, Alert } from "@/components/ui";
import { percent, labelEvent } from "@/lib/utils";
import type {
  LabCase, LabReport, CaseResult, Trace, Runtime,
} from "@/types";

// ---------------------------------------------------------------------------
// Plain-language event labels for trace
// ---------------------------------------------------------------------------
const TRACE_LABELS: Record<string, { label: string; detail?: string }> = {
  document_ingested:          { label: "Document uploaded",               detail: "Content extracted and chunked" },
  document_retrieval:         { label: "Document information retrieved",  detail: "Relevant chunks fetched via vector search" },
  memory_guard_decision:      { label: "Memory reviewed by Memory Guard" },
  memory_lifecycle_transition:{ label: "Memory status updated" },
  action_proposed:            { label: "Assistant proposed an action" },
  sentinel_finding:           { label: "Security concern identified",     detail: "Sentinel advisory check ran" },
  gateway_decision:           { label: "Action decision recorded" },
  gateway_authorized:         { label: "Action approved",                detail: "Policy gateway authorised execution" },
  gateway_denied:             { label: "Unsafe action blocked",           detail: "Policy gateway denied execution" },
  tool_execution_completed:   { label: "Simulated action executed" },
};

// ---------------------------------------------------------------------------
// CaseResultRow — expandable result card
// ---------------------------------------------------------------------------
function CaseResultRow({ result }: { result: CaseResult }) {
  const [open, setOpen] = useState(false);

  const isError   = result.status === "error";
  const isBenign  = result.kind === "benign";
  const succeeded = isBenign ? result.legitimate_task_success : result.attack_success;

  let summaryLabel: string;
  let summaryVariant: "neutral" | "danger" | "success" | "warning";

  if (isError) {
    summaryLabel   = "Error";
    summaryVariant = "warning";
  } else if (isBenign) {
    summaryLabel   = succeeded ? "Task completed" : "Task incomplete";
    summaryVariant = succeeded ? "success" : "warning";
  } else {
    summaryLabel   = result.attack_success ? "Attack succeeded" : "Attack blocked";
    summaryVariant = result.attack_success ? "danger" : "success";
  }

  return (
    <div className="border-t border-slate-800">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="flex w-full items-center gap-3 py-3 px-1 text-left hover:bg-slate-900/40 transition-colors rounded"
      >
        <Badge variant={summaryVariant}>{summaryLabel}</Badge>
        <span className="flex-1 text-sm text-slate-300">
          {result.case_id}
          <span className="ml-2 text-xs text-slate-500">/ {result.configuration}</span>
        </span>
        {result.memory_states.length > 0 && (
          <span className="text-xs text-slate-500">
            Memory: {result.memory_states.join(", ")}
          </span>
        )}
        {open ? <ChevronUp className="h-4 w-4 text-slate-500" /> : <ChevronDown className="h-4 w-4 text-slate-500" />}
      </button>

      {open && (
        <div className="pb-4 px-1 space-y-2">
          {/* Plain-language summary */}
          <div className="rounded-lg bg-slate-950 p-3 text-sm text-slate-300">
            {result.attack_success && !isBenign && (
              <p className="text-rose-300">
                ⚠ The attack succeeded. A malicious memory reached trusted state, a report was
                delivered to the attacker address, or the workflow was hijacked. This is expected
                for the <strong>baseline</strong> (no-defense) configuration.
              </p>
            )}
            {!result.attack_success && !isBenign && (
              <p className="text-cyan-300">
                ✓ The attack was blocked. Memory Guard, Sentinel, or the Policy Gateway prevented
                the unsafe outcome from occurring.
              </p>
            )}
            {isBenign && (
              <p className="text-slate-400">
                This is a benign control case — no attack was attempted.
                {result.legitimate_task_success ? " The task completed successfully." : " The task did not complete."}
              </p>
            )}
            {result.attacker_delivery && (
              <p className="mt-1 text-rose-300">
                Simulated report was delivered to attacker address.
              </p>
            )}
          </div>

          {/* Trace */}
          {result.trace.length > 0 && (
            <ol className="space-y-1.5">
              {result.trace.map((event: Trace, idx) => {
                const info = TRACE_LABELS[event.type];
                return (
                  <li key={idx} className="rounded-lg bg-slate-950 px-3 py-2 text-xs">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-slate-200">{info?.label ?? labelEvent(event.type)}</span>
                      {event.outcome && (
                        <Badge variant={
                          ["denied", "quarantined"].includes(event.outcome) ? "danger"
                          : event.outcome === "trusted" ? "success"
                          : "neutral"
                        }>
                          {event.outcome}
                        </Badge>
                      )}
                    </div>
                    {info?.detail && <p className="mt-0.5 text-slate-500">{info.detail}</p>}
                    {event.reason && <p className="mt-0.5 text-slate-500">Reason: {event.reason}</p>}
                    <details className="mt-1">
                      <summary className="cursor-pointer text-slate-600 hover:text-slate-400">
                        Technical details ({event.type})
                      </summary>
                      <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-all text-slate-500 text-[11px]">
                        {JSON.stringify(event.payload, null, 2)}
                      </pre>
                    </details>
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ReportView
// ---------------------------------------------------------------------------
function ReportView({ report }: { report: LabReport }) {
  function download() {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(report, null, 2)], { type: "application/json" })
    );
    const a = document.createElement("a");
    a.href = url; a.download = `${report.run_id}.json`; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  const isScripted = report.evidence_kind === "scripted";

  return (
    <Panel>
      <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
        <div>
          <h3 className="font-semibold text-slate-100">
            Results · {isScripted ? "Scripted evidence" : "Live model evidence"}
          </h3>
          <p className="mt-1 text-xs text-slate-500 break-all">
            Run {report.run_id} · Corpus SHA-256 {report.corpus_sha256}
          </p>
        </div>
        <Button variant="ghost" onClick={download}>
          <Download className="h-4 w-4" />
          Download JSON
        </Button>
      </div>

      {/* Metric summary table */}
      <div className="overflow-x-auto mb-6">
        <table className="w-full text-left text-sm">
          <thead>
            <tr>
              {["Defense", "Cases", "Attacks blocked", "Benign success", "Errors", "Avg time"].map(h => (
                <th key={h} className="px-3 py-2 text-xs text-slate-400">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {report.metrics.map(m => {
              const blockRate = m.attack_success_rate === null ? "—"
                : percent(1 - m.attack_success_rate);
              return (
                <tr key={m.configuration} className="border-t border-slate-800">
                  <td className="px-3 py-2 font-medium text-slate-200">{m.configuration}</td>
                  <td className="px-3 py-2 text-slate-400">{m.cases}</td>
                  <td className="px-3 py-2">
                    <span className={m.attack_success_rate === 0 ? "text-cyan-300" : "text-rose-300"}>
                      {blockRate}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-slate-400">{percent(m.legitimate_task_success_rate)}</td>
                  <td className="px-3 py-2 text-slate-400">{m.errors}</td>
                  <td className="px-3 py-2 text-slate-400">{m.mean_latency_ms.toFixed(0)} ms</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <p className="mb-3 text-xs text-slate-500">
        {isScripted
          ? "Scripted evidence tests deterministic security controls — it does not measure real AI robustness. Errors are excluded from rates."
          : "Live model evidence uses actual model output. Small sample sizes limit statistical confidence."}
      </p>

      {/* Per-case results */}
      <div>
        {report.results.map(r => (
          <CaseResultRow key={`${r.case_id}-${r.configuration}`} result={r} />
        ))}
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// AttackLabPage
// ---------------------------------------------------------------------------
const DEFENSE_CONFIGS = ["baseline", "memory_guard", "gateway", "full"] as const;

export default function AttackLabPage() {
  const cache    = useQueryClient();
  const runtime  = useQuery({ queryKey: ["runtime"], queryFn: () => api<Runtime>("runtime") });
  const cases    = useQuery({ queryKey: ["lab-cases"], queryFn: () => api<{ items: LabCase[] }>("lab/cases") });
  const history  = useQuery({ queryKey: ["lab-runs"], queryFn: () => api<{ items: Omit<LabReport, "results">[] }>("lab/runs") });

  const [caseId,    setCaseId]    = useState("memory_poisoning-00");
  const [split,     setSplit]     = useState("all");
  const [mode,      setMode]      = useState<"offline" | "live">("offline");
  const [defenses,  setDefenses]  = useState<string[]>(["baseline", "full"]);
  const [report,    setReport]    = useState<LabReport | null>(null);

  const run = useMutation({
    mutationFn: (ids: string[]) => api<LabReport>("lab/runs", { case_ids: ids, configurations: defenses, mode }),
    onSuccess: data => { setReport(data); void cache.invalidateQueries(); },
  });
  const replay = useMutation({
    mutationFn: (id: string) => api<LabReport>(`lab/runs/${id}`),
    onSuccess: setReport,
  });

  const activeCase = cases.data?.items.find(c => c.id === caseId);
  const suite = (cases.data?.items ?? []).filter(c => split === "all" || c.split === split);
  const isLive = runtime.data?.mode === "live";

  function toggleDefense(config: string) {
    setDefenses(prev =>
      prev.includes(config) ? prev.filter(d => d !== config) : [...prev, config]
    );
  }

  return (
    <div className="space-y-6">
      {/* Intro */}
      <Panel>
        <div className="flex items-start gap-3">
          <FlaskConical className="mt-0.5 h-5 w-5 shrink-0 text-cyan-300" aria-hidden />
          <div>
            <h2 className="font-semibold text-slate-100">Attack Lab</h2>
            <p className="mt-1 text-sm text-slate-400">
              Test how the AI agent responds to malicious documents, prompt injections, and
              memory-poisoning attempts. Experiments run in an isolated harness with synthetic data —
              no real memory or tools are affected.
            </p>
          </div>
        </div>
      </Panel>

      {/* Configuration panel */}
      <Panel>
        <h3 className="mb-4 font-semibold text-slate-100">Experiment Configuration</h3>

        <div className="grid gap-5 md:grid-cols-2">
          {/* Scenario selector */}
          <div>
            <label className="mb-1.5 block text-sm text-slate-300" htmlFor="case-select">
              Attack scenario
            </label>
            <Select
              id="case-select"
              className="w-full"
              value={caseId}
              onChange={e => setCaseId(e.target.value)}
            >
              {cases.data?.items.map(c => (
                <option key={c.id} value={c.id}>
                  {c.id} · {c.kind} · {c.split}
                </option>
              ))}
            </Select>
          </div>

          {/* Mode selector */}
          <div>
            <label className="mb-1.5 block text-sm text-slate-300" htmlFor="mode-select">
              Evidence generation
            </label>
            <Select
              id="mode-select"
              className="w-full"
              value={mode}
              onChange={e => setMode(e.target.value as "offline" | "live")}
            >
              <option value="offline">Scripted — deterministic, no API cost (recommended)</option>
              <option value="live" disabled={!isLive}>
                Live model — uses API credits{!isLive ? " (switch to live mode first)" : ""}
              </option>
            </Select>
            {mode === "live" && isLive && runtime.data && (
              <p className="mt-1.5 text-xs text-cyan-300/80">
                Attacker: {runtime.data.mode} · Model: {runtime.data.reviewer ?? "configured model"}
              </p>
            )}
          </div>
        </div>

        {/* Scenario content preview */}
        {activeCase && (
          <div className="mt-4">
            <p className="mb-1.5 text-xs text-slate-500 uppercase tracking-wide">
              Attack document preview
            </p>
            <div className="rounded-lg bg-slate-950 p-3 text-sm text-slate-300 whitespace-pre-wrap max-h-36 overflow-y-auto font-mono leading-relaxed">
              {activeCase.document.content}
            </div>
          </div>
        )}

        {/* Defense configuration */}
        <div className="mt-5">
          <p className="mb-2 text-sm text-slate-300">Defense profiles to compare</p>
          <div className="flex flex-wrap gap-3">
            {DEFENSE_CONFIGS.map(config => (
              <label
                key={config}
                className="flex cursor-pointer items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-300 hover:border-slate-600 transition-colors has-[:checked]:border-cyan-700 has-[:checked]:text-cyan-200"
              >
                <input
                  type="checkbox"
                  checked={defenses.includes(config)}
                  onChange={() => toggleDefense(config)}
                  className="accent-cyan-400"
                />
                {config.replaceAll("_", " ")}
              </label>
            ))}
          </div>
          {defenses.includes("baseline") && (
            <p className="mt-2 text-xs text-amber-300/70">
              Baseline disables defenses. It is provided for controlled comparison only,
              never as a real assistant configuration.
            </p>
          )}
        </div>

        {/* Action area */}
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <Button
            disabled={run.isPending || !defenses.length || !activeCase}
            onClick={() => run.mutate([caseId])}
          >
            <Play className="h-3.5 w-3.5" />
            {run.isPending ? "Running…" : "Run this scenario"}
          </Button>

          <div className="flex items-center gap-2">
            <label className="text-sm text-slate-400" htmlFor="split-select">Suite:</label>
            <Select
              id="split-select"
              value={split}
              onChange={e => setSplit(e.target.value)}
            >
              <option value="all">All 60 cases</option>
              <option value="development">Development 30</option>
              <option value="heldout">Held-out 30</option>
            </Select>
            <Button
              variant="ghost"
              disabled={run.isPending || !defenses.length || !suite.length || mode !== "offline"}
              onClick={() => run.mutate(suite.map(c => c.id))}
            >
              Run {suite.length}-case suite
            </Button>
          </div>
        </div>

        {run.isPending && (
          <p role="status" className="mt-3 text-sm text-cyan-200">
            Experiment running — each case is saved as it completes…
          </p>
        )}
        {(run.error || cases.error) && (
          <Alert className="mt-3">{(run.error ?? cases.error)?.message ?? "Unknown error"}</Alert>
        )}
      </Panel>

      {/* Results */}
      {report && <ReportView report={report} />}

      {/* Saved runs */}
      {(history.data?.items.length ?? 0) > 0 && (
        <Panel>
          <h3 className="mb-3 font-semibold text-slate-100">Previous Runs</h3>
          <div className="space-y-1">
            {history.data?.items.map(item => (
              <button
                key={item.run_id}
                disabled={replay.isPending || run.isPending}
                onClick={() => replay.mutate(item.run_id)}
                className="block w-full rounded-lg px-3 py-2 text-left text-sm text-cyan-200 hover:bg-slate-900 transition-colors disabled:opacity-40"
              >
                {item.run_id}
                <span className="ml-2 text-xs text-slate-500">{item.evidence_kind}</span>
              </button>
            ))}
          </div>
        </Panel>
      )}
    </div>
  );
}
