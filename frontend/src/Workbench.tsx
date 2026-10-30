import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

const panel = "rounded-xl border border-slate-800 bg-slate-900/60 p-5";
const input = "w-full rounded-lg border border-slate-700 bg-slate-950 p-3 text-sm";
const button = "rounded-lg bg-cyan-300 px-4 py-2 text-sm font-semibold text-slate-950 disabled:opacity-40";
type Document = { id: string; content: string; source_type: string; trust_level: string };
type Runtime = { mode: "offline" | "live"; reviewer: string; tools: string };
type TaskResult = { status: string; reply: string; steps: number; tool_results: unknown[] };

function Failure({ error }: { error: Error | null }) {
  return error ? <p role="alert" className="my-3 text-sm text-rose-300">{error.message}</p> : null;
}

export function AssistantWorkspace() {
  const cache = useQueryClient();
  const runtime = useQuery({ queryKey: ["runtime"], queryFn: () => api<Runtime>("runtime") });
  const [task, setTask] = useState("Summarize the vendor note and remember useful facts.");
  const [source, setSource] = useState<Document>({ id: "pasted-email", content: "", source_type: "email", trust_level: "untrusted" });
  const [sessionId] = useState(() => crypto.randomUUID());
  const loadSource = useMutation({ mutationFn: () => api<{ items: Document[] }>("documents"),
    onSuccess: data => { if (data.items[0]) setSource(data.items[0]); },
  });
  const run = useMutation({
    mutationFn: () => api<TaskResult>("assistant/run", { task, session_id: sessionId,
      task_id: crypto.randomUUID(), documents: source.content.trim() ? [source] : [] }),
    onSuccess: () => { void cache.invalidateQueries(); },
  });
  return <div className="grid gap-5 xl:grid-cols-2">
    <form className={panel} onSubmit={event => { event.preventDefault(); run.mutate(); }}>
      <p className="mb-4 text-sm text-slate-400">Mode: <strong className="text-cyan-200">{runtime.data?.mode ?? "loading"}</strong>. Offline mode extracts text without an AI call. Memory still passes through the real review workflow.</p>
      <label className="mb-2 block text-sm" htmlFor="assistant-task">Your task</label>
      <textarea id="assistant-task" className={input} value={task} maxLength={8000} onChange={event => setTask(event.target.value)} required />
      <label className="mb-2 mt-4 block text-sm" htmlFor="source-text">Synthetic email or document</label>
      <textarea id="source-text" className={`${input} min-h-48`} value={source.content} maxLength={16000}
        onChange={event => setSource({ ...source, content: event.target.value })} />
      <p className="my-2 text-xs text-slate-500">Leave empty to test recall from reviewed memories. Editing a registered document makes its provenance untrusted.</p>
      <div className="my-4 flex flex-wrap gap-3">
        <button type="button" className="text-sm text-cyan-200" disabled={loadSource.isPending} onClick={() => loadSource.mutate()}>Load verified example</button>
        <button type="button" className="text-sm text-slate-400" onClick={() => setSource({ ...source, content: "" })}>Clear document for recall</button>
      </div>
      <Failure error={run.error ?? loadSource.error ?? runtime.error} />
      <button className={button} disabled={run.isPending || !task.trim()}>{run.isPending ? "Running…" : "Run assistant"}</button>
    </form>
    <section className={panel} aria-live="polite"><h3 className="mb-4 font-semibold">Assistant response</h3>
      {run.data ? <><p className="mb-3 text-xs text-cyan-200">{run.data.status} · {run.data.steps} step(s)</p><p className="whitespace-pre-wrap text-sm text-slate-300">{run.data.reply}</p><p className="mt-6 text-sm text-slate-400">Review proposed memories in Memories. Actions appear in Tool calls and Approvals.</p></> : <p className="text-sm text-slate-500">Run a task, review its proposed memory, then clear the document and ask a follow-up.</p>}
    </section>
  </div>;
}

type LabCase = { id: string; kind: string; objective: string; split: string; document: Document };
type Trace = { type: string; outcome: string | null; reason: string | null; payload: unknown };
type CaseResult = { case_id: string; configuration: string; kind: string; status: string; attack_success: boolean; legitimate_task_success: boolean | null; poisoned_memory: boolean; attacker_delivery: boolean; memory_states: string[]; trace: Trace[] };
type Metric = { configuration: string; cases: number; errors: number; attack_success_rate: number | null; attacker_delivery_rate: number | null; legitimate_task_success_rate: number | null; mean_latency_ms: number };
type Report = { run_id: string; evidence_kind: string; corpus_sha256: string; storage: string; metrics: Metric[]; results: CaseResult[] };
const configurations = ["baseline", "memory_guard", "gateway", "full"];
const percent = (value: number | null) => value === null ? "—" : `${Math.round(value * 100)}%`;

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
