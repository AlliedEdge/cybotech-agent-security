/**
 * EvaluationsPage — persisted security metrics with baseline vs. full-defense comparison.
 * Mirrors EvaluationWorkbench "evaluations" view from Workbench.tsx, redesigned.
 */

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { api } from "@/api";
import { percent } from "@/lib/utils";
import { Panel, Select, Button } from "@/components/ui";
import type { EvaluationSummary, EvaluationMetric, Proportion, EvaluationRun } from "@/types";

function MetricCard({ label, metric }: { label: string; metric: Proportion | undefined }) {
  if (!metric) return (
    <article className="rounded-lg bg-slate-950 p-4">
      <p className="text-xs text-slate-400">{label}</p>
      <p className="mt-2 text-sm text-slate-500">—</p>
    </article>
  );
  if (!metric.available) return (
    <article className="rounded-lg bg-slate-950 p-4">
      <p className="text-xs text-slate-400">{label}</p>
      <p className="mt-2 text-sm text-amber-300/80">Unavailable</p>
      <p className="text-xs text-slate-500 mt-0.5">{metric.unavailability_reason ?? "Insufficient data"}</p>
    </article>
  );
  const ci = metric.confidence_interval_95;
  return (
    <article className="rounded-lg bg-slate-950 p-4">
      <p className="text-xs text-slate-400">{label}</p>
      <p className="mt-2 text-2xl font-semibold">{percent(metric.percent)}</p>
      <p className="mt-1 text-xs text-slate-500">
        {metric.numerator}/{metric.denominator} valid experiments
        {ci ? ` · 95% CI ${(ci[0] * 100).toFixed(1)}–${(ci[1] * 100).toFixed(1)}%` : ""}
      </p>
    </article>
  );
}

export default function EvaluationsPage() {
  const cache  = useQueryClient();
  const [configuration, setConfiguration] = useState("");
  const [mode, setMode] = useState("");

  const params = new URLSearchParams();
  if (configuration) params.set("configuration", configuration);
  if (mode)          params.set("generation_mode", mode);
  const suffix = params.toString() ? `?${params}` : "";

  const summary    = useQuery({ queryKey: ["evaluation-summary", configuration, mode],    queryFn: () => api<EvaluationSummary>(`evaluations/summary${suffix}`) });
  const metrics    = useQuery({ queryKey: ["evaluation-metrics", configuration, mode],    queryFn: () => api<{ items: EvaluationMetric[] }>(`evaluations/metrics${suffix}`) });
  const comparable = useQuery({ queryKey: ["evaluation-comparability", mode], queryFn: () => api<{ comparability: { comparable: boolean; mismatches: string[]; baseline_count: number; treatment_count: number } }>(`evaluations/comparability${mode ? `?generation_mode=${mode}` : ""}`) });
  const runs       = useQuery({ queryKey: ["evaluation-runs"], queryFn: () => api<{ items: EvaluationRun[] }>("evaluations/runs") });

  const allMetrics = metrics.data?.items.filter(m => m.population_label === "all") ?? [];
  const metric = (name: string) => allMetrics.find(m => m.metric_name === name);

  return (
    <div className="space-y-6">
      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <Select value={configuration} onChange={e => setConfiguration(e.target.value)}>
          <option value="">All defense profiles</option>
          <option value="baseline">Baseline</option>
          <option value="full">Full defense</option>
          <option value="memory_guard">Memory Guard</option>
          <option value="gateway">Gateway</option>
        </Select>
        <Select value={mode} onChange={e => setMode(e.target.value)}>
          <option value="">All generation modes</option>
          <option value="scripted">Scripted</option>
          <option value="live_model">Live model</option>
        </Select>
        <Button variant="ghost" onClick={() => void cache.invalidateQueries({ queryKey: ["evaluation"] })}>
          <RefreshCw className="h-3.5 w-3.5" />
          Refresh
        </Button>
      </div>

      {/* Summary */}
      <Panel>
        <div className="flex flex-wrap gap-6 text-sm">
          <div>
            <p className="text-xs text-slate-400">Total experiments</p>
            <p className="mt-1 text-2xl font-semibold">{summary.data?.total_experiment_records ?? "—"}</p>
          </div>
          <div>
            <p className="text-xs text-slate-400">Valid attacks</p>
            <p className="mt-1 text-2xl font-semibold">{summary.data?.valid_attack_records ?? "—"}</p>
          </div>
          <div>
            <p className="text-xs text-slate-400">Valid benign</p>
            <p className="mt-1 text-2xl font-semibold">{summary.data?.valid_benign_records ?? "—"}</p>
          </div>
          <div>
            <p className="text-xs text-slate-400">Errors</p>
            <p className="mt-1 text-2xl font-semibold">{summary.data?.failed_records ?? "—"}</p>
          </div>
        </div>
      </Panel>

      {/* Metric cards */}
      <Panel>
        <h2 className="mb-4 font-semibold text-slate-100">Security Metrics</h2>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          <MetricCard label="Attack success rate"         metric={metric("attack_success_rate")} />
          <MetricCard label="Memory poisoning"            metric={metric("memory_poisoning_success_rate")} />
          <MetricCard label="Unauthorized tool execution" metric={metric("unauthorized_tool_execution_rate")} />
          <MetricCard label="Detection rate"              metric={metric("detection_rate")} />
          <MetricCard label="False positive rate"         metric={metric("false_positive_rate")} />
        </div>
        <p className="mt-4 text-xs text-amber-300/70">
          Metrics are calculated from persisted evidence. Unavailable values reflect insufficient data — never zero.
        </p>
      </Panel>

      {/* Comparability */}
      <Panel>
        <h2 className="mb-3 font-semibold text-slate-100">Baseline vs. Full Defense</h2>
        {comparable.data?.comparability.comparable ? (
          <div className="space-y-1 text-sm">
            <p className="text-cyan-200">
              Comparable populations available: {comparable.data.comparability.baseline_count} baseline
              / {comparable.data.comparability.treatment_count} full-defense records.
            </p>
          </div>
        ) : (
          <div className="text-sm text-amber-300/80">
            <p>Not yet a controlled comparison.</p>
            {comparable.data?.comparability.mismatches.length ? (
              <ul className="mt-2 list-disc pl-5 space-y-1">
                {comparable.data.comparability.mismatches.map(m => <li key={m}>{m}</li>)}
              </ul>
            ) : null}
            <p className="mt-2 text-slate-500">Run both baseline and full-defense on the same scenario set to enable comparison.</p>
          </div>
        )}
        <p className="mt-4 text-xs text-slate-500">
          Runs available: {runs.data?.items.length ?? "—"}.
          Use the Experiments page for individual case evidence.
        </p>
      </Panel>
    </div>
  );
}
