/**
 * TimelinePage — full paginated audit event log with plain-language descriptions.
 * Also exposes the tool-call filtered view.
 */

import { useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { Clock } from "lucide-react";
import { base } from "@/api";
import { Panel, Badge } from "@/components/ui";
import { formatTime, labelEvent } from "@/lib/utils";
import type { EventRow, Page } from "@/types";

const TOOL_EVENT_TYPES = new Set([
  "action_proposed", "sentinel_finding", "gateway_authorized", "gateway_denied",
  "gateway_decision", "approval_requested", "approval_decision", "tool_execution_completed",
]);

async function fetchEvents(offset: number): Promise<Page<EventRow>> {
  const r = await fetch(`${base}/api/v1/security-events?limit=100&offset=${offset}`);
  if (!r.ok) throw new Error(`API ${r.status}`);
  return r.json() as Promise<Page<EventRow>>;
}

function EventRow({ row }: { row: EventRow }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="border-t border-slate-800 py-2.5">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="flex w-full flex-wrap items-start gap-3 text-left"
      >
        <span className="mt-0.5 whitespace-nowrap text-xs text-slate-500 w-36 shrink-0">
          {formatTime(row.timestamp)}
        </span>
        <span className="flex-1 min-w-0">
          <span className="text-sm text-slate-200">{labelEvent(row.type)}</span>
          <span className="ml-2 text-xs text-slate-500">{row.type}</span>
        </span>
        {row.outcome && (
          <Badge variant={
            ["denied", "quarantined"].includes(row.outcome) ? "danger"
            : row.outcome === "trusted" ? "success"
            : "neutral"
          }>
            {row.outcome}
          </Badge>
        )}
      </button>

      {open && (
        <div className="mt-2 rounded-lg bg-slate-950 p-3 text-xs space-y-1">
          {row.reason_code && <p className="text-slate-400">Reason: <span className="text-slate-300">{row.reason_code}</span></p>}
          {row.actor && <p className="text-slate-400">Actor: <span className="text-slate-300">{row.actor}</span></p>}
          {row.object_id && <p className="text-slate-400">Object: <span className="font-mono text-slate-300">{row.object_id}</span></p>}
          {row.correlation_id && <p className="text-slate-400">Run: <span className="font-mono text-slate-300">{row.correlation_id}</span></p>}
          {row.payload && (
            <details>
              <summary className="cursor-pointer text-slate-600 hover:text-slate-400">Raw payload</summary>
              <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all text-slate-500 text-[11px]">
                {JSON.stringify(row.payload, null, 2)}
              </pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

export default function TimelinePage() {
  const [view, setView] = useState<"all" | "actions">("all");

  const events = useInfiniteQuery({
    queryKey: ["events"],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => fetchEvents(pageParam as number),
    getNextPageParam: last => last.offset + last.limit < last.total ? last.offset + last.limit : undefined,
  });

  const allRows = events.data?.pages.flatMap(p => p.items) ?? [];
  const total   = events.data?.pages[0]?.total ?? 0;
  const rows    = view === "actions" ? allRows.filter(r => TOOL_EVENT_TYPES.has(r.type)) : allRows;

  return (
    <div className="space-y-5">
      <Panel>
        <div className="flex items-start gap-3 mb-4">
          <Clock className="mt-0.5 h-5 w-5 shrink-0 text-cyan-300" aria-hidden />
          <div>
            <h2 className="font-semibold text-slate-100">Security Timeline</h2>
            <p className="mt-1 text-sm text-slate-400">
              Complete audit log of every security-relevant decision. Click any event to expand details.
            </p>
          </div>
        </div>

        {/* View toggle */}
        <div className="mb-4 flex gap-1 rounded-lg bg-slate-950 p-1 w-fit">
          {(["all", "actions"] as const).map(v => (
            <button
              key={v}
              type="button"
              onClick={() => setView(v)}
              className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                view === v
                  ? "bg-slate-800 text-slate-100"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              {v === "all" ? `All events (${total})` : "Tool & action events"}
            </button>
          ))}
        </div>

        {rows.length === 0 ? (
          <p className="py-6 text-center text-sm text-slate-500">No persisted events yet.</p>
        ) : (
          rows.map(row => <EventRow key={row.id} row={row} />)
        )}

        {events.hasNextPage && (
          <button
            onClick={() => void events.fetchNextPage()}
            disabled={events.isFetchingNextPage}
            className="mt-4 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-400 hover:text-slate-200 hover:border-slate-600 transition-colors disabled:opacity-40"
          >
            {events.isFetchingNextPage ? "Loading…" : "Load more"}
          </button>
        )}
      </Panel>
    </div>
  );
}
