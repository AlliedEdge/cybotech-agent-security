/**
 * ExperimentsPage — paginated experiment table with trace evidence drill-down.
 * Mirrors EvaluationWorkbench "explorer" and "control" views.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";
import { api } from "@/api";
import { Panel, Select, Button, Badge, Alert } from "@/components/ui";
import { labelEvent } from "@/lib/utils";
import type { EvaluationExperiment, LabCase, Trace } from "@/types";

// ---------------------------------------------------------------------------
// Run a single bounded scripted experiment
// ---------------------------------------------------------------------------
function RunExperimentPanel() {
  const cache = useQueryClient();
  const cases = useQuery({ queryKey: ["evaluation-cases"], queryFn: () => api<{ items: LabCase[] }>("lab/cases") });
  const [caseId,  setCaseId]  = useState("memory_poisoning-00");
  const [profile, setProfile] = useState("full");

  const run = useMutation({
    mutationFn: () => api<{ run_id: string }>("evaluations/run", {
      case_ids: [caseId], configurations: [profile], mode: "offline",
    }),
    onSuccess: () => void cache.invalidateQueries(),
  });

  return (
    <Panel>
      <h3 className="mb-1 font-semibold text-slate-100">Run a Scripted Experiment</h3>
      <p className="mb-4 text-sm text-slate-400">
        Run one synthetic case through the isolated harness. Results are persisted and appear
        in the table below. Live-model generation is not available here.
      </p>
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label className="mb-1.5 block text-sm text-slate-300" htmlFor="exp-case">Scenario</label>
          <Select id="exp-case" className="w-full" value={caseId} onChange={e => setCaseId(e.target.value)}>
            {cases.data?.items.map(c => (
              <option key={c.id} value={c.id}>{c.id} · {c.kind}</option>
            ))}
          </Select>
        </div>
        <div>
          <label className="mb-1.5 block text-sm text-slate-300" htmlFor="exp-profile">Defense profile</label>
          <Select id="exp-profile" className="w-full" value={profile} onChange={e => setProfile(e.target.value)}>
            <option value="full">Full defense</option>
            <option value="baseline">Baseline (comparison only)</option>
            <option value="memory_guard">Memory Guard only</option>
            <option value="gateway">Gateway only</option>
          </Select>
        </div>
      </div>
      {profile === "baseline" && (
        <p className="mt-2 text-xs text-amber-300/70">
          Baseline disables defenses. Available for controlled comparison only.
        </p>
      )}
      {run.error && <Alert className="mt-3">{run.error.message}</Alert>}
      <Button
        className="mt-4"
        disabled={run.isPending || !cases.data}
        onClick={() => run.mutate()}
      >
        {run.isPending ? "Running…" : "Run experiment"}
      </Button>
      {run.data && (
        <p className="mt-3 text-sm text-cyan-200">
          Persisted run {run.data.run_id}. It will appear in the table below.
        </p>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Experiment detail pane
// ---------------------------------------------------------------------------
type DetailData = EvaluationExperiment & {
  trace?: Trace[];
  memory_states?: string[];
  attacker_delivery?: boolean;
  poisoned_memory?: boolean;
};

function ExperimentDetail({ experiment }: { experiment: EvaluationExperiment }) {
  const detail = useQuery({
    queryKey: ["evaluation-detail", experiment.run_id, experiment.case_id],
    queryFn: () => api<DetailData>(`evaluations/experiments/${experiment.run_id}/${experiment.case_id}`),
  });

  return (
    <Panel>
      <h3 className="mb-2 font-semibold text-slate-100">
        Evidence · {experiment.case_id} / {experiment.configuration}
      </h3>
      <div className="mb-3 flex flex-wrap gap-3 text-sm text-slate-400">
        <span>Outcome: <strong className="text-slate-200">{detail.data?.outcome ?? "loading…"}</strong></span>
        <span>Mode: {experiment.generation_mode}</span>
        <span>Model: {experiment.model_name}</span>
        {detail.data?.attacker_delivery && (
          <Badge variant="danger">Attacker delivery</Badge>
        )}
        {detail.data?.memory_states?.map(s => (
          <Badge key={s} variant={s === "trusted" ? "danger" : "neutral"}>{s}</Badge>
        ))}
      </div>

      {detail.data?.trace?.length ? (
        <ol className="space-y-1.5">
          {detail.data.trace.map((event, idx) => (
            <li key={idx} className="rounded-lg bg-slate-950 px-3 py-2 text-xs">
              <div className="flex items-center gap-2">
                <span className="font-medium text-slate-200">{labelEvent(event.type)}</span>
                {event.outcome && (
                  <Badge variant={
                    ["denied","quarantined"].includes(event.outcome) ? "danger"
                    : event.outcome === "trusted" ? "success" : "neutral"
                  }>
                    {event.outcome}
                  </Badge>
                )}
              </div>
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
          ))}
        </ol>
      ) : (
        <p className="text-sm text-amber-300/70">
          Trace evidence unavailable or incomplete for this record.
        </p>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// ExperimentsPage
// ---------------------------------------------------------------------------
export default function ExperimentsPage() {
  const cache = useQueryClient();
  const [configuration, setConfiguration] = useState("");
  const [mode, setMode] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<EvaluationExperiment | null>(null);

  const params = new URLSearchParams();
  if (configuration) params.set("configuration", configuration);
  if (mode)          params.set("generation_mode", mode);
  const suffix = params.toString() ? `&${params}` : "";

  const experiments = useQuery({
    queryKey: ["evaluation-experiments", configuration, mode, offset],
    queryFn: () => api<{ items: EvaluationExperiment[]; total: number }>(
      `evaluations/experiments?limit=20&offset=${offset}${suffix}`
    ),
  });

  const total = experiments.data?.total ?? 0;

  return (
    <div className="space-y-6">
      <RunExperimentPanel />

      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-3">
        <Select value={configuration} onChange={e => { setConfiguration(e.target.value); setOffset(0); }}>
          <option value="">All profiles</option>
          <option value="baseline">Baseline</option>
          <option value="full">Full defense</option>
          <option value="memory_guard">Memory Guard</option>
          <option value="gateway">Gateway</option>
        </Select>
        <Select value={mode} onChange={e => { setMode(e.target.value); setOffset(0); }}>
          <option value="">All modes</option>
          <option value="scripted">Scripted</option>
          <option value="live_model">Live model</option>
        </Select>
        <Button variant="ghost" onClick={() => void cache.invalidateQueries({ queryKey: ["evaluation-experiments"] })}>
          <RefreshCw className="h-3.5 w-3.5" />
          Refresh
        </Button>
        <span className="ml-auto text-xs text-slate-500">{total} records</span>
      </div>

      {/* Table */}
      <Panel className="overflow-x-auto p-0">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-slate-800">
              {["Experiment", "Scenario", "Profile", "Mode", "Outcome", ""].map(h => (
                <th key={h} className="px-4 py-3 text-xs text-slate-400">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {experiments.data?.items.map(item => (
              <tr
                key={`${item.run_id}-${item.case_id}`}
                className="border-t border-slate-800 hover:bg-slate-900/40 transition-colors"
              >
                <td className="px-4 py-2.5 font-mono text-xs text-slate-400">
                  {item.run_id.slice(0, 14)}
                </td>
                <td className="px-4 py-2.5">
                  <p className="text-slate-200">{item.case_id}</p>
                  <p className="text-xs text-slate-500">{item.scenario_objective}</p>
                </td>
                <td className="px-4 py-2.5 text-slate-300">{item.configuration}</td>
                <td className="px-4 py-2.5">
                  <p className="text-slate-300">{item.generation_mode}</p>
                  <p className="text-xs text-slate-500">{item.model_name}</p>
                </td>
                <td className="px-4 py-2.5">
                  <Badge variant={
                    item.outcome === "attack_succeeded" ? "danger"
                    : item.outcome === "attack_contained" ? "success"
                    : "neutral"
                  }>
                    {item.outcome.replaceAll("_", " ")}
                  </Badge>
                </td>
                <td className="px-4 py-2.5">
                  <button
                    className="text-xs text-cyan-200 hover:text-cyan-100"
                    onClick={() => setSelected(s => s?.run_id === item.run_id && s.case_id === item.case_id ? null : item)}
                  >
                    {selected?.run_id === item.run_id && selected.case_id === item.case_id
                      ? "Hide" : "Inspect"}
                  </button>
                </td>
              </tr>
            ))}
            {!experiments.data?.items.length && (
              <tr>
                <td colSpan={6} className="px-4 py-6 text-center text-sm text-slate-500">
                  No experiments match these filters. Run one above.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Panel>

      {/* Pagination */}
      {total > 20 && (
        <div className="flex items-center gap-3">
          <Button variant="ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>
            <ChevronLeft className="h-4 w-4" /> Previous
          </Button>
          <span className="text-xs text-slate-500">{offset + 1}–{Math.min(offset + 20, total)} of {total}</span>
          <Button variant="ghost" disabled={offset + 20 >= total} onClick={() => setOffset(offset + 20)}>
            Next <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      )}

      {/* Detail pane */}
      {selected && <ExperimentDetail experiment={selected} />}
    </div>
  );
}
