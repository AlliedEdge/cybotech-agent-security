/**
 * OverviewPage — key metrics from persisted experiment and memory data.
 * Shows only real API data. Unavailable metrics are shown as unavailable, not zero.
 */

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { base } from "@/api";
import { api } from "@/api";
import { Panel, Badge } from "@/components/ui";
import { percent, formatTime, labelEvent } from "@/lib/utils";
import type {
  EvaluationSummary, EvaluationMetric, Proportion,
  EventRow, Page, Summary,
} from "@/types";

// ---------------------------------------------------------------------------
// MetricCard
// ---------------------------------------------------------------------------
function MetricCard({ label, metric }: { label: string; metric: Proportion | undefined }) {
  if (!metric) {
    return (
      <article className="rounded-lg bg-slate-950 p-4">
        <p className="text-xs text-slate-400">{label}</p>
        <p className="mt-2 text-sm text-slate-500">—</p>
      </article>
    );
  }
  if (!metric.available) {
    return (
      <article className="rounded-lg bg-slate-950 p-4">
        <p className="text-xs text-slate-400">{label}</p>
        <p className="mt-2 text-sm text-amber-300/80">Unavailable</p>
        <p className="mt-1 text-xs text-slate-500">{metric.unavailability_reason ?? "Insufficient evidence"}</p>
      </article>
    );
  }
  const ci = metric.confidence_interval_95;
  return (
    <article className="rounded-lg bg-slate-950 p-4">
      <p className="text-xs text-slate-400">{label}</p>
      <p className="mt-2 text-2xl font-semibold text-slate-100">{percent(metric.percent)}</p>
      <p className="mt-1 text-xs text-slate-500">
        {metric.numerator}/{metric.denominator} experiments
        {ci ? ` · 95% CI ${(ci[0] * 100).toFixed(1)}–${(ci[1] * 100).toFixed(1)}%` : ""}
      </p>
    </article>
  );
}

// ---------------------------------------------------------------------------
// RecentEventsTable
// ---------------------------------------------------------------------------
function RecentEventsTable({ rows }: { rows: EventRow[] }) {
  if (!rows.length) {
    return <p className="p-5 text-sm text-slate-500">No security events yet.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr>
            {["Time", "Event", "Outcome"].map(h => (
              <th key={h} className="px-4 py-3 text-xs uppercase text-slate-500">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(row => (
            <tr key={row.id} className="border-t border-slate-800/80">
              <td className="whitespace-nowrap px-4 py-2.5 text-xs text-slate-400">
                {formatTime(row.timestamp)}
              </td>
              <td className="px-4 py-2.5 text-sm text-slate-300">
                {labelEvent(row.type)}
              </td>
              <td className="px-4 py-2.5">
                {row.outcome && (
                  <Badge variant={
                    ["denied", "quarantined"].includes(row.outcome) ? "danger"
                    : row.outcome === "trusted" ? "success"
                    : "neutral"
                  }>
                    {row.outcome}
                  </Badge>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${base}/api/v1/${path}`);
  if (!r.ok) throw new Error(`API ${r.status}`);
  return r.json() as Promise<T>;
}

export default function OverviewPage() {
  const summary   = useQuery({ queryKey: ["dashboard-summary"],    queryFn: () => get<Summary>("dashboard/summary") });
  const evalSum   = useQuery({ queryKey: ["evaluation-summary"],   queryFn: () => api<EvaluationSummary>("evaluations/summary") });
  const metrics   = useQuery({ queryKey: ["evaluation-metrics"],   queryFn: () => api<{ items: EvaluationMetric[] }>("evaluations/metrics") });
  const events    = useInfiniteQuery({
    queryKey: ["events"],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => get<Page<EventRow>>(`security-events?limit=100&offset=${pageParam}`),
    getNextPageParam: last => last.offset + last.limit < last.total ? last.offset + last.limit : undefined,
  });

  const recentEvents = (events.data?.pages.flatMap(p => p.items) ?? []).slice(0, 10);
  const allMetrics   = metrics.data?.items.filter(m => m.population_label === "all") ?? [];
  const metric       = (name: string) => allMetrics.find(m => m.metric_name === name);

  const trusted   = summary.data?.memory_counts?.trusted ?? 0;
  const eventTotal = summary.data?.event_total ?? 0;

  return (
    <div className="space-y-6">
      {/* Inventory counts */}
      <div className="grid gap-3 sm:grid-cols-3">
        <Panel className="p-4">
          <p className="text-xs text-slate-400">Trusted memories</p>
          <p className="mt-2 text-3xl font-semibold text-slate-100">{trusted}</p>
        </Panel>
        <Panel className="p-4">
          <p className="text-xs text-slate-400">Audit events</p>
          <p className="mt-2 text-3xl font-semibold text-slate-100">{eventTotal}</p>
        </Panel>
        <Panel className="p-4">
          <p className="text-xs text-slate-400">Experiments run</p>
          <p className="mt-2 text-3xl font-semibold text-slate-100">
            {evalSum.data?.total_experiment_records ?? "—"}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            {evalSum.data?.valid_attack_records ?? "—"} valid attacks
          </p>
        </Panel>
      </div>

      {/* Security metrics */}
      <Panel>
        <h2 className="mb-4 font-semibold text-slate-100">Security Metrics</h2>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          <MetricCard label="Attack success rate"           metric={metric("attack_success_rate")} />
          <MetricCard label="Memory poisoning rate"         metric={metric("memory_poisoning_success_rate")} />
          <MetricCard label="Unauthorized tool execution"   metric={metric("unauthorized_tool_execution_rate")} />
          <MetricCard label="Detection rate"                metric={metric("detection_rate")} />
          <MetricCard label="False positive rate"           metric={metric("false_positive_rate")} />
        </div>
        <p className="mt-4 text-xs text-amber-300/70">
          Exploratory evidence only. Sample sizes and confidence intervals are shown where available.
          Unavailable metrics indicate insufficient persisted data — not zero.
        </p>
      </Panel>

      {/* Recent events */}
      <Panel>
        <h2 className="mb-1 font-semibold text-slate-100">Recent Security Events</h2>
        <p className="mb-4 text-xs text-slate-400">Last 10 persisted events from the audit log.</p>
        <RecentEventsTable rows={recentEvents} />
      </Panel>
    </div>
  );
}
