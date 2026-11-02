/**
 * MemoryPage — Documents and long-term agent memory.
 *
 * Two clear sections:
 *  - Long-term Agent Memory: persisted facts with provenance, trust state, approval controls
 *  - Approvals: human-in-the-loop tool authorization queue
 */

import { useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronUp, Database, ShieldCheck } from "lucide-react";
import { base, api } from "@/api";
import { Panel, Button, Badge, Alert } from "@/components/ui";
import { formatTime } from "@/lib/utils";
import type { MemoryRow, Page, Approval, Action } from "@/types";

// ---------------------------------------------------------------------------
// Memory row with expandable provenance
// ---------------------------------------------------------------------------
function MemoryCard({ memory, onChanged }: { memory: MemoryRow; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  async function act(action: "approve" | "reject" | "revoke") {
    if (!window.confirm(`${action[0].toUpperCase()}${action.slice(1)} this memory? This decision will be recorded.`)) return;
    setBusy(true);
    try {
      const body = action === "revoke"
        ? { reviewer: "local_demo_reviewer" }
        : { approved: action === "approve", reviewer: "local_demo_reviewer" };
      const r = await fetch(`${base}/api/v1/memories/${memory.id}/${action}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error();
      onChanged();
    } catch {
      window.alert("The memory action failed. The backend did not confirm a state change.");
    } finally {
      setBusy(false);
    }
  }

  const statusVariant: Record<string, "neutral" | "success" | "warning" | "danger"> = {
    trusted: "success", pending: "warning", quarantined: "danger",
    rejected: "neutral", revoked: "neutral", expired: "neutral",
  };

  return (
    <div className="border-t border-slate-800 py-3">
      <div className="flex flex-wrap items-start gap-3">
        <Badge variant={statusVariant[memory.status] ?? "neutral"}>{memory.status}</Badge>
        <div className="flex-1 min-w-0">
          <p className="text-sm text-slate-200 line-clamp-2">{memory.content}</p>
          <p className="mt-0.5 text-xs text-slate-500">
            {memory.category} · {memory.source_type} · {formatTime(memory.created_at)}
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {["pending", "quarantined"].includes(memory.status) && (
            <>
              <Button variant="ghost" disabled={busy} onClick={() => void act("approve")}
                className="text-cyan-300 text-xs !px-2 !py-1">Approve</Button>
              <Button variant="danger" disabled={busy} onClick={() => void act("reject")}
                className="text-xs !px-2 !py-1">Reject</Button>
            </>
          )}
          {memory.status === "trusted" && (
            <Button variant="danger" disabled={busy} onClick={() => void act("revoke")}
              className="text-xs !px-2 !py-1">Revoke</Button>
          )}
          <button
            type="button"
            onClick={() => setOpen(o => !o)}
            className="text-slate-500 hover:text-slate-300"
            aria-label={open ? "Collapse" : "Expand"}
          >
            {open ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </button>
        </div>
      </div>

      {open && (
        <div className="mt-3 rounded-lg bg-slate-950 p-3 text-xs space-y-1">
          <p className="text-slate-400">Source: <span className="text-slate-300">{memory.source_type}: {memory.source_id}</span></p>
          <p className="text-slate-400">Trust level: <span className="text-slate-300">{String(memory.provenance?.trust_level ?? "unverified")}</span></p>
          <p className="text-slate-400">Decision reason: <span className="text-slate-300">{memory.reason}</span></p>
          <details>
            <summary className="cursor-pointer text-slate-600 hover:text-slate-400">Raw provenance</summary>
            <pre className="mt-1 whitespace-pre-wrap break-all text-slate-500 text-[11px]">
              {JSON.stringify(memory.provenance, null, 2)}
            </pre>
          </details>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Approvals queue
// ---------------------------------------------------------------------------
function ApprovalQueue() {
  const cache = useQueryClient();
  const [notice, setNotice] = useState("");

  const approvals  = useQuery({ queryKey: ["approvals"], queryFn: () => api<{ items: Approval[] }>("approvals") });
  const proposals  = useQuery({ queryKey: ["action-proposals"], queryFn: () => api<{ items: { id: string; payload: { action?: Action } }[] }>("security-events?event_type=action_proposed&limit=50") });

  const mutate = useMutation({
    mutationFn: ({ path, body }: { path: string; body: unknown }) => api(path, body),
    onSuccess: (_, vars) => {
      setNotice(vars.path.endsWith("/execute") ? "Simulated report delivered. The approval is now consumed." : "Decision recorded.");
      void cache.invalidateQueries();
    },
  });

  function createSample() {
    mutate.mutate({ path: "approvals", body: {
      tool_name: "send_report",
      arguments: { destination: "reports@internal.example", content: "SYNTHETIC weekly maintenance report" },
      proposed_by: "primary_assistant", session_id: "local-review",
      task_id: crypto.randomUUID(), user_task_description: "Send the weekly maintenance report",
      retrieved_memory_ids: [],
    }});
  }

  const pendingProposals = proposals.data?.items.filter(i => i.payload.action?.tool_name === "send_report") ?? [];

  return (
    <Panel>
      <div className="flex items-start gap-3 mb-4">
        <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-cyan-300" aria-hidden />
        <div>
          <h3 className="font-semibold text-slate-100">Action Approvals</h3>
          <p className="mt-1 text-sm text-slate-400">
            Review proposed tool calls before they execute. Each approval is single-use and expires.
            All executions stay within the simulator.
          </p>
        </div>
      </div>

      <Button onClick={createSample} disabled={mutate.isPending} variant="ghost">
        Create sample approval request
      </Button>
      {notice && <p role="status" className="mt-3 text-sm text-cyan-200">{notice}</p>}
      {mutate.error && <Alert className="mt-3">{mutate.error.message}</Alert>}

      {/* Proposals from assistant */}
      {pendingProposals.map(item => (
        <div key={item.id} className="mt-4 rounded-lg bg-slate-950 p-3">
          <p className="text-xs text-slate-400 mb-2">Assistant proposed this action:</p>
          <pre className="whitespace-pre-wrap text-sm text-slate-300 mb-3">
            {JSON.stringify(item.payload.action?.arguments, null, 2)}
          </pre>
          <Button disabled={mutate.isPending} onClick={() => mutate.mutate({ path: "approvals", body: item.payload.action })}>
            Request review for this action
          </Button>
        </div>
      ))}

      {/* Approval queue */}
      {approvals.data?.items.map(item => (
        <div key={item.id} className="mt-4 rounded-lg border border-slate-800 p-4">
          <div className="flex items-center gap-2 mb-2">
            <Badge variant={item.consumed_at ? "neutral" : item.status === "pending" ? "warning" : "success"}>
              {item.consumed_at ? "consumed" : item.status}
            </Badge>
            <span className="text-xs text-slate-500">expires {formatTime(item.expires_at)}</span>
          </div>
          <p className="text-sm text-slate-300 mb-3">{item.action?.user_task_description ?? "No description"}</p>
          <pre className="mb-4 whitespace-pre-wrap break-all text-sm text-slate-400 rounded-lg bg-slate-950 p-3">
            {JSON.stringify(item.action?.arguments ?? {}, null, 2)}
          </pre>
          <div className="flex gap-2">
            {item.status === "pending" && (
              <>
                <Button disabled={mutate.isPending} onClick={() => mutate.mutate({ path: `approvals/${item.id}/decision`, body: { approved: true, reviewer: "local_demo_reviewer" } })}>
                  Approve
                </Button>
                <Button variant="danger" disabled={mutate.isPending} onClick={() => mutate.mutate({ path: `approvals/${item.id}/decision`, body: { approved: false, reviewer: "local_demo_reviewer" } })}>
                  Deny
                </Button>
              </>
            )}
            {item.status === "approved" && !item.consumed_at && (
              <Button disabled={mutate.isPending} onClick={() => mutate.mutate({ path: `approvals/${item.id}/execute`, body: {} })}>
                Execute approved simulation
              </Button>
            )}
          </div>
        </div>
      ))}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// MemoryPage
// ---------------------------------------------------------------------------
export default function MemoryPage() {
  const cache  = useQueryClient();
  const memories = useInfiniteQuery({
    queryKey: ["memories"],
    initialPageParam: 0,
    queryFn: async ({ pageParam }) => {
      const r = await fetch(`${base}/api/v1/memories?limit=100&offset=${pageParam}`);
      if (!r.ok) throw new Error(`API ${r.status}`);
      return r.json() as Promise<Page<MemoryRow>>;
    },
    getNextPageParam: last => last.offset + last.limit < last.total ? last.offset + last.limit : undefined,
  });

  const rows = memories.data?.pages.flatMap(p => p.items) ?? [];

  function onChanged() {
    void cache.invalidateQueries({ queryKey: ["memories"] });
    void cache.invalidateQueries({ queryKey: ["dashboard-summary"] });
  }

  return (
    <div className="space-y-6">
      {/* Memory section */}
      <Panel>
        <div className="flex items-start gap-3 mb-4">
          <Database className="mt-0.5 h-5 w-5 shrink-0 text-cyan-300" aria-hidden />
          <div>
            <h2 className="font-semibold text-slate-100">Long-term Agent Memory</h2>
            <p className="mt-1 text-sm text-slate-400">
              Facts the assistant has proposed to remember across sessions. Each memory carries
              provenance and trust state. Untrusted memories cannot be retrieved by the assistant.
            </p>
          </div>
        </div>

        {rows.length === 0 && !memories.isLoading ? (
          <p className="text-sm text-slate-500">No persisted memories yet.</p>
        ) : (
          rows.map(m => (
            <MemoryCard key={m.id} memory={m} onChanged={onChanged} />
          ))
        )}
        {memories.hasNextPage && (
          <Button
            variant="ghost"
            className="mt-4"
            disabled={memories.isFetchingNextPage}
            onClick={() => void memories.fetchNextPage()}
          >
            {memories.isFetchingNextPage ? "Loading…" : "Load more"}
          </Button>
        )}
      </Panel>

      {/* Approvals */}
      <ApprovalQueue />
    </div>
  );
}
