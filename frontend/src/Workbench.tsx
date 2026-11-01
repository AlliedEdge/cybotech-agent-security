import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

const panel = "rounded-xl border border-slate-800 bg-slate-900/60 p-5";
const input = "w-full rounded-lg border border-slate-700 bg-slate-950 p-3 text-sm";
const button = "rounded-lg bg-cyan-300 px-4 py-2 text-sm font-semibold text-slate-950 disabled:opacity-40";
type Document = { id: string; content: string; source_type: string; trust_level: string };
type Runtime = { mode: "offline" | "live"; reviewer: string; tools: string };

function Failure({ error }: { error: Error | null }) {
  return error ? <p role="alert" className="my-3 text-sm text-rose-300">{error.message}</p> : null;
}

type LabCase = { id: string; kind: string; objective: string; split: string; document: Document };
type Trace = { type: string; outcome: string | null; reason: string | null; payload: unknown };
type CaseResult = { case_id: string; configuration: string; kind: string; status: string; attack_success: boolean; legitimate_task_success: boolean | null; poisoned_memory: boolean; attacker_delivery: boolean; memory_states: string[]; trace: Trace[] };
type Metric = { configuration: string; cases: number; errors: number; attack_success_rate: number | null; attacker_delivery_rate: number | null; legitimate_task_success_rate: number | null; mean_latency_ms: number };
type Report = { run_id: string; evidence_kind: string; corpus_sha256: string; storage: string; metrics: Metric[]; results: CaseResult[] };
const configurations = ["baseline", "memory_guard", "gateway", "full"];
const percent = (value: number | null) => value === null ? "—" : `${Math.round(value * 100)}%`;

type Proportion = { available: boolean; numerator: number | null; denominator: number | null; percent: number | null; confidence_interval_95: [number, number] | null; unavailability_reason: string | null };
type EvaluationMetric = { population_label: string; metric_name: string } & Proportion;
type EvaluationSummary = { total_experiment_records: number; valid_attack_records: number; valid_benign_records: number; failed_records: number; incomplete_records: number; invalid_records: number; strata: Array<{ population_label: string; outcome_counts: Record<string, number> }> };
type EvaluationExperiment = { run_id: string; case_id: string; scenario_kind: string; scenario_objective: string; configuration: string; generation_mode: string; model_name: string; run_timestamp: string; execution_duration_ms: number; status: string; outcome: string; defense_profile: Record<string, unknown>; errors: string[] };
type EvaluationRun = { run_id: string; corpus_version: string; evidence_kind: string; case_count: number; occurred_at: string | null };

function MetricValue({ metric }: { metric: Proportion | undefined }) {
  if (!metric) return <p className="text-sm text-slate-500">No metric population returned.</p>;
  if (!metric.available) return <p className="text-sm text-amber-200">Unavailable · {metric.unavailability_reason ?? "insufficient evidence"}</p>;
  const ci = metric.confidence_interval_95;
  return <><p className="text-2xl font-semibold">{metric.percent}%</p><p className="mt-1 text-xs text-slate-400">{metric.numerator}/{metric.denominator} valid experiments{ci ? ` · 95% CI ${(ci[0] * 100).toFixed(1)}–${(ci[1] * 100).toFixed(1)}%` : ""}</p></>;
}

export function EvaluationWorkbench({ view }: { view: "overview" | "evaluations" | "explorer" | "control" }) {
  const cache = useQueryClient();
  const [configuration, setConfiguration] = useState("");
  const [mode, setMode] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<EvaluationExperiment | null>(null);
  const query = new URLSearchParams(); if (configuration) query.set("configuration", configuration); if (mode) query.set("generation_mode", mode);
  const suffix = query.toString() ? `?${query}` : "";
  const summary = useQuery({ queryKey: ["evaluation-summary", configuration, mode], queryFn: () => api<EvaluationSummary>(`evaluations/summary${suffix}`) });
  const metrics = useQuery({ queryKey: ["evaluation-metrics", configuration, mode], queryFn: () => api<{ items: EvaluationMetric[] }>(`evaluations/metrics${suffix}`) });
  const comparable = useQuery({ queryKey: ["evaluation-comparability", mode], queryFn: () => api<{ comparability: { comparable: boolean; mismatches: string[]; baseline_count: number; treatment_count: number } }>(`evaluations/comparability${mode ? `?generation_mode=${mode}` : ""}`) });
  const experiments = useQuery({ queryKey: ["evaluation-experiments", configuration, mode, offset], queryFn: () => api<{ items: EvaluationExperiment[]; total: number }>(`evaluations/experiments?limit=20&offset=${offset}${configuration ? `&configuration=${configuration}` : ""}${mode ? `&generation_mode=${mode}` : ""}`) });
  const detail = useQuery({ queryKey: ["evaluation-detail", selected?.run_id, selected?.case_id], enabled: !!selected, queryFn: () => api<EvaluationExperiment & { trace?: Trace[]; memory_states?: string[]; attacker_delivery?: boolean; poisoned_memory?: boolean }>(`evaluations/experiments/${selected?.run_id}/${selected?.case_id}`) });
  const runs = useQuery({ queryKey: ["evaluation-runs"], queryFn: () => api<{ items: EvaluationRun[] }>("evaluations/runs") });
  const cases = useQuery({ queryKey: ["evaluation-cases"], queryFn: () => api<{ items: LabCase[] }>("lab/cases") });
  const [caseId, setCaseId] = useState("memory_poisoning-00");
  const [profile, setProfile] = useState("full");
  const run = useMutation({ mutationFn: () => api<{ run_id: string }>("evaluations/run", { case_ids: [caseId], configurations: [profile], mode: "offline" }), onSuccess: () => void cache.invalidateQueries() });
  const allMetrics = metrics.data?.items.filter(item => item.population_label === "all") ?? [];
  const metric = (name: string) => allMetrics.find(item => item.metric_name === name);
  const filters = <div className="flex flex-wrap gap-3"><label className="text-xs text-slate-400">Defense<select className="ml-2 rounded bg-slate-950 p-2 text-sm" value={configuration} onChange={e => { setConfiguration(e.target.value); setOffset(0); }}><option value="">All profiles</option><option value="baseline">Baseline</option><option value="full">Full defense</option><option value="memory_guard">Memory Guard</option><option value="gateway">Gateway</option></select></label><label className="text-xs text-slate-400">Generation<select className="ml-2 rounded bg-slate-950 p-2 text-sm" value={mode} onChange={e => { setMode(e.target.value); setOffset(0); }}><option value="">All modes</option><option value="scripted">Scripted</option><option value="live_model">Live model</option></select></label><button className="text-sm text-cyan-200" onClick={() => void cache.invalidateQueries({ queryKey: ["evaluation"] })}>Refresh</button></div>;
  if (view === "control") return <section className={panel}><h3 className="font-semibold">Bounded scripted experiment</h3><p className="my-3 text-sm text-slate-400">This local-demo control runs one synthetic case through the isolated experiment harness. It cannot alter production assistant defenses. Live-model execution is intentionally not exposed here.</p><div className="grid gap-4 md:grid-cols-2"><label className="text-sm">Scenario<select className={`${input} mt-2`} value={caseId} onChange={e => setCaseId(e.target.value)}>{cases.data?.items.map(c => <option key={c.id} value={c.id}>{c.id} · {c.kind}</option>)}</select></label><label className="text-sm">Defense profile<select className={`${input} mt-2`} value={profile} onChange={e => setProfile(e.target.value)}><option value="full">Full defense</option><option value="baseline">Baseline (isolated lab only)</option></select></label></div><p className="mt-3 text-xs text-amber-200">Baseline is available only for isolated evaluation comparison; it is never a normal assistant setting.</p><Failure error={run.error ?? cases.error} /><button className={`${button} mt-4`} disabled={run.isPending || !cases.data} onClick={() => run.mutate()}>{run.isPending ? "Running bounded experiment…" : "Run scripted experiment"}</button>{run.data ? <p className="mt-4 text-sm text-cyan-200">Persisted run {run.data.run_id}. Open Attack Explorer to inspect its evidence.</p> : null}</section>;
  if (view === "explorer") return <div className="space-y-5"><section className={panel}><div className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="font-semibold">Attack Explorer</h3><p className="mt-1 text-sm text-slate-400">Authoritative outcomes are separate from graph execution status.</p></div>{filters}</div><Failure error={experiments.error} /><div className="mt-4 overflow-auto"><table className="w-full text-left text-sm"><thead><tr>{["Experiment", "Scenario", "Profile", "Mode / model", "Outcome", "Status"].map(x => <th className="p-2 text-xs text-slate-400" key={x}>{x}</th>)}</tr></thead><tbody>{experiments.data?.items.map(item => <tr className="border-t border-slate-800" key={`${item.run_id}-${item.case_id}`}><td className="p-2 font-mono text-xs">{item.run_id.slice(0, 14)}</td><td>{item.case_id}<br /><span className="text-xs text-slate-500">{item.scenario_objective}</span></td><td>{item.configuration}</td><td>{item.generation_mode}<br /><span className="text-xs text-slate-500">{item.model_name}</span></td><td>{item.outcome}</td><td><button className="text-cyan-200" onClick={() => setSelected(item)}>{item.status} · inspect</button></td></tr>)}</tbody></table></div>{!experiments.data?.items.length && <p className="mt-4 text-sm text-slate-500">No persisted experiments match these filters.</p>}<div className="mt-4 flex gap-3"><button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>Previous</button><button disabled={!experiments.data || offset + 20 >= experiments.data.total} onClick={() => setOffset(offset + 20)}>Next</button></div></section>{selected && <section className={panel}><h3 className="font-semibold">Evidence · {selected.case_id}</h3><p className="my-2 text-sm text-slate-400">Outcome: {detail.data?.outcome ?? "loading"} · execution: {detail.data?.status ?? "loading"}</p>{detail.data?.trace?.length ? <ol className="space-y-2">{detail.data.trace.map((event, index) => <li className="rounded bg-slate-950 p-3 text-xs" key={index}><strong>{event.type}</strong> · {event.outcome ?? event.reason ?? "recorded"}<pre className="mt-2 whitespace-pre-wrap break-all text-slate-400">{JSON.stringify(event.payload, null, 2)}</pre></li>)}</ol> : <p className="text-sm text-amber-200">Trace evidence is unavailable or incomplete for this persisted record.</p>}</section>}</div>;
  return <div className="space-y-5"><section className={panel}><div className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="font-semibold">Persisted evaluation state</h3><p className="mt-1 text-sm text-slate-400">Metrics are calculated from application evidence; unavailable values are never rendered as zero.</p></div>{filters}</div><Failure error={summary.error ?? metrics.error ?? comparable.error} /><div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4"><article className="rounded-lg bg-slate-950 p-4"><p className="text-xs text-slate-400">Persisted experiments</p><p className="mt-2 text-2xl">{summary.data?.total_experiment_records ?? "—"}</p><p className="text-xs text-slate-500">Valid attacks {summary.data?.valid_attack_records ?? "—"}</p></article>{[["Attack success", "attack_success_rate"], ["Memory poisoning", "memory_poisoning_success_rate"], ["Tool execution", "unauthorized_tool_execution_rate"], ["Detection", "detection_rate"], ["False positives", "false_positive_rate"]].map(([label, name]) => <article key={name} className="rounded-lg bg-slate-950 p-4"><p className="text-xs text-slate-400">{label}</p><MetricValue metric={metric(name)} /></article>)}</div><p className="mt-4 text-xs text-amber-200">Exploratory evidence only: sample sizes and confidence intervals are shown where the backend provides them.</p></section>{view === "evaluations" && <section className={panel}><h3 className="font-semibold">Baseline versus full defense</h3>{comparable.data?.comparability.comparable ? <p className="mt-2 text-sm text-cyan-200">Comparable populations: {comparable.data.comparability.baseline_count} baseline / {comparable.data.comparability.treatment_count} full-defense records.</p> : <div className="mt-2 text-sm text-amber-200"><p>Not a controlled comparison.</p><ul className="mt-2 list-disc pl-5">{comparable.data?.comparability.mismatches.map(x => <li key={x}>{x}</li>)}</ul></div>}<p className="mt-4 text-sm text-slate-400">Runs available: {runs.data?.items.length ?? "—"}. Use Attack Explorer for individual evidence and the backend’s exact provenance.</p></section>}</div>;
}

export function AttackLab() {
  const cache = useQueryClient();
  const cases = useQuery({ queryKey: ["lab-cases"], queryFn: () => api<{ items: LabCase[] }>("lab/cases") });
  const history = useQuery({ queryKey: ["lab-runs"], queryFn: () => api<{ items: Omit<Report, "results">[] }>("lab/runs") });
  const runtime = useQuery({ queryKey: ["runtime"], queryFn: () => api<Runtime>("runtime") });
  const [caseId, setCaseId] = useState("memory_poisoning-00");
  const [split, setSplit] = useState("all");
  const [mode, setMode] = useState("offline");
  const [selected, setSelected] = useState(["baseline", "full"]);
  const [report, setReport] = useState<Report | null>(null);
  const generated = useMutation({ mutationFn: () => api<{ status: string; termination_reason: string; attack_success: boolean; victim_model: string; payload: { content: string; provider: string } | null }>("lab/generated-attack", { case_id: caseId, configuration: "full" }), onSuccess: () => { void cache.invalidateQueries(); } });
  const run = useMutation({ mutationFn: (ids: string[]) => api<Report>("lab/runs", { case_ids: ids, configurations: selected, mode }),
    onSuccess: data => { setReport(data); void cache.invalidateQueries(); },
  });
  const replay = useMutation({ mutationFn: (id: string) => api<Report>(`lab/runs/${id}`), onSuccess: setReport });
  const activeCase = cases.data?.items.find(item => item.id === caseId);
  const suite = cases.data?.items.filter(item => split === "all" || item.split === split) ?? [];
  function download() {
    if (!report) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = `${report.run_id}.json`; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <div className="space-y-5">
    <section className={panel}><h3 className="font-semibold">Vibe Hacker · generate an attack</h3><p className="my-3 text-sm text-slate-400">Generate a synthetic document and test it against the isolated live-model assistant and configured reviewers. Live calls can incur charges.</p><button className={button} disabled={generated.isPending || runtime.data?.mode !== "live" || activeCase?.kind !== "attack"} onClick={() => generated.mutate()}>{generated.isPending ? "Generating…" : "Generate and launch attack"}</button>{runtime.data?.mode !== "live" && <p className="mt-3 text-xs text-amber-200">Configure live model mode to enable attack generation.</p>}<Failure error={generated.error} />{generated.data && <div className="mt-4 rounded-lg bg-slate-950 p-4 text-sm"><p>Status: {generated.data.status} · {generated.data.termination_reason}</p><p>Victim: {generated.data.victim_model} · attack success: {String(generated.data.attack_success)}</p>{generated.data.payload && <><p className="mt-2 text-xs text-slate-400">Generated by {generated.data.payload.provider}</p><pre className="mt-2 whitespace-pre-wrap break-words">{generated.data.payload.content}</pre></>}</div>}</section>
    <section className={panel}><h3 className="font-semibold">Attack → memory → fresh session → policy decision</h3>
      <p className="my-3 text-sm text-slate-400">Experiments use synthetic data, isolated memory, and simulated tools. Scripted mode deliberately follows attacks to test the controls. Its scores do not measure real AI robustness.</p>
      <div className="grid gap-4 md:grid-cols-2">
        <label className="text-sm">Scenario<select className={`${input} mt-2`} value={caseId} onChange={event => setCaseId(event.target.value)}>{cases.data?.items.map(item => <option key={item.id} value={item.id}>{item.id} · {item.split}</option>)}</select></label>
        <label className="text-sm">Evidence mode<select className={`${input} mt-2`} value={mode} onChange={event => setMode(event.target.value)}><option value="offline">Scripted control test · no API key</option><option value="live" disabled={runtime.data?.mode !== "live"}>Live primary model · uses API credits</option></select></label>
      </div>
      <p className="my-4 whitespace-pre-wrap rounded-lg bg-slate-950 p-4 text-sm text-slate-300">{activeCase?.document.content ?? "Loading scenarios…"}</p>
      <fieldset className="my-4 flex flex-wrap gap-4"><legend className="mb-2 text-sm">Defense configurations</legend>{configurations.map(configuration => <label key={configuration} className="flex gap-2 text-sm"><input type="checkbox" checked={selected.includes(configuration)} onChange={event => setSelected(current => event.target.checked ? [...current, configuration] : current.filter(item => item !== configuration))} />{configuration.replaceAll("_", " ")}</label>)}</fieldset>
      <div className="flex flex-wrap items-center gap-4">
        <button className={button} disabled={run.isPending || !selected.length || !activeCase} onClick={() => run.mutate([caseId])}>Compare selected scenario</button>
        <label className="text-sm">Suite split <select className="ml-2 rounded bg-slate-800 p-2" value={split} onChange={event => setSplit(event.target.value)}><option value="all">All 60 cases</option><option value="development">Development 30</option><option value="heldout">Held-out 30</option></select></label>
        <button className={button} disabled={run.isPending || !selected.length || !suite.length || mode !== "offline"} onClick={() => run.mutate(suite.map(item => item.id))}>Run {suite.length}-case evaluation</button>
      </div>
      {run.isPending ? <p role="status" className="mt-4 text-cyan-200">Experiment running. Each case is saved as it completes; watch Audit history for progress.</p> : null}
      <Failure error={run.error ?? replay.error ?? cases.error ?? history.error} />
    </section>
    {report ? <section className={panel} aria-live="polite"><div className="flex flex-wrap justify-between gap-3"><h3 className="font-semibold">Comparison · {report.evidence_kind === "scripted" ? "SCRIPTED EVIDENCE" : "LIVE MODEL EVIDENCE"}</h3><button className="text-sm text-cyan-200" onClick={download}>Download report JSON</button></div>
      <p className="my-3 break-all text-xs text-slate-500">Run {report.run_id} · {report.storage} · Corpus SHA-256 {report.corpus_sha256}</p>
      <div className="overflow-auto"><table className="w-full text-left text-sm"><thead><tr>{["Configuration", "Cases", "Attack success", "Attacker delivery", "Benign task success", "Errors", "Mean time"].map(label => <th className="p-3" key={label}>{label}</th>)}</tr></thead><tbody>{report.metrics.map(metric => <tr className="border-t border-slate-800" key={metric.configuration}><td className="p-3">{metric.configuration}</td><td>{metric.cases}</td><td>{percent(metric.attack_success_rate)}</td><td>{percent(metric.attacker_delivery_rate)}</td><td>{percent(metric.legitimate_task_success_rate)}</td><td>{metric.errors}</td><td>{metric.mean_latency_ms} ms</td></tr>)}</tbody></table></div>
      <p className="my-3 text-xs text-slate-400">Errors are reported separately and excluded from success-rate denominators. Memory-poisoning success means a malicious memory became trusted; disclosure and workflow cases require a simulated delivery.</p>
      <div className="max-h-[36rem] overflow-auto">{report.results.map(result => <details key={`${result.case_id}-${result.configuration}`} className="border-t border-slate-800 py-3"><summary className="cursor-pointer text-sm">{result.case_id} / {result.configuration} · {result.status === "error" ? "ERROR" : result.kind === "benign" ? (result.legitimate_task_success ? "task succeeded" : "task incomplete") : result.attack_success ? "attack succeeded" : "attack stopped"}</summary><p className="my-2 text-xs text-slate-400">Memory: {result.memory_states.join(", ") || "none"} · Attacker delivery: {String(result.attacker_delivery)}</p><ol className="space-y-2">{result.trace.map((event, index) => <li key={index} className="rounded bg-slate-950 p-3 text-xs"><strong>{event.type}</strong> · {event.outcome ?? event.reason ?? "recorded"}<details><summary className="mt-1 cursor-pointer text-slate-400">Evidence</summary><pre className="mt-2 whitespace-pre-wrap break-all">{JSON.stringify(event.payload, null, 2)}</pre></details></li>)}</ol></details>)}</div>
    </section> : null}
    <section className={panel}><h3 className="mb-3 font-semibold">Saved runs</h3>{history.data?.items.length ? history.data.items.map(item => <button key={item.run_id} disabled={replay.isPending || run.isPending} className="mb-2 block text-left text-sm text-cyan-200" onClick={() => replay.mutate(item.run_id)}>{item.run_id} · {item.evidence_kind}</button>) : <p className="text-sm text-slate-500">No completed experiments yet.</p>}</section>
  </div>;
}

type Action = { tool_name: string; arguments: Record<string, unknown>; proposed_by: string; session_id: string; task_id: string; user_task_description: string; retrieved_memory_ids: string[]; approval_id?: string | null };
type Approval = { id: string; status: string; expires_at: string; consumed_at: string | null; action: Action | null };

export function ApprovalQueue() {
  const cache = useQueryClient();
  const [notice, setNotice] = useState("");
  const approvals = useQuery({ queryKey: ["approvals"], queryFn: () => api<{ items: Approval[] }>("approvals") });
  const proposals = useQuery({ queryKey: ["action-proposals"], queryFn: () => api<{ items: { id: string; payload: { action?: Action } }[] }>("security-events?event_type=action_proposed&limit=50") });
  const mutate = useMutation({ mutationFn: ({ path, body }: { path: string; body: unknown }) => api(path, body),
    onSuccess: (_, variables) => { setNotice(variables.path.endsWith("/execute") ? "Simulated report delivered. The approval is now consumed." : "Decision recorded."); void cache.invalidateQueries(); },
  });
  function sample() {
    mutate.mutate({ path: "approvals", body: { tool_name: "send_report", arguments: { destination: "reports@internal.example", content: "SYNTHETIC weekly maintenance report" }, proposed_by: "primary_assistant", session_id: "local-review", task_id: crypto.randomUUID(), user_task_description: "Send the weekly maintenance report", retrieved_memory_ids: [] } });
  }
  return <div className="space-y-5"><section className={panel}><h3 className="font-semibold">Human approval for simulated reports</h3><p className="my-3 text-sm text-slate-400">Review the exact recipient, content, and task. Approval is bound to that action, expires, and can be used once. All deliveries stay inside the simulator.</p><button className={button} disabled={mutate.isPending} onClick={sample}>Create sample approval request</button><Failure error={mutate.error ?? approvals.error ?? proposals.error} /><p role="status" className="mt-3 text-sm text-cyan-200">{notice}</p></section>
    {proposals.data?.items.filter(item => item.payload.action?.tool_name === "send_report").map(item => <article key={item.id} className={panel}><h3>Assistant proposal</h3><pre className="my-3 whitespace-pre-wrap text-sm">{JSON.stringify(item.payload.action?.arguments, null, 2)}</pre><button className={button} disabled={mutate.isPending} onClick={() => mutate.mutate({ path: "approvals", body: item.payload.action })}>Request review for this action</button></article>)}
    {approvals.data?.items.map(item => <article key={item.id} className={panel}><p className="text-xs text-cyan-200">{item.consumed_at ? "consumed" : item.status} · expires {new Date(item.expires_at).toLocaleString()}</p><p className="my-3 text-sm">{item.action?.user_task_description ?? "Legacy action details unavailable"}</p><pre className="mb-4 whitespace-pre-wrap break-all text-sm text-slate-300">{JSON.stringify(item.action?.arguments ?? {}, null, 2)}</pre><div className="flex gap-3">{item.status === "pending" ? <><button className={button} disabled={mutate.isPending} onClick={() => mutate.mutate({ path: `approvals/${item.id}/decision`, body: { approved: true, reviewer: "local_demo_reviewer" } })}>Approve exact action</button><button className="text-sm text-rose-300" disabled={mutate.isPending} onClick={() => mutate.mutate({ path: `approvals/${item.id}/decision`, body: { approved: false, reviewer: "local_demo_reviewer" } })}>Deny</button></> : null}{item.status === "approved" && !item.consumed_at ? <button className={button} disabled={mutate.isPending} onClick={() => mutate.mutate({ path: `approvals/${item.id}/execute`, body: {} })}>Execute approved simulation</button> : null}</div></article>)}
  </div>;
}
