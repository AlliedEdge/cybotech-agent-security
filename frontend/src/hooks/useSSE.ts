import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { base } from "@/api";

const SSE_EVENT_TYPES = [
  "lab_run_completed",
  "lab_case_completed",
  "task_failed",
  "memory_guard_decision",
  "memory_lifecycle_transition",
  "action_proposed",
  "sentinel_finding",
  "gateway_decision",
  "gateway_authorized",
  "gateway_denied",
  "approval_requested",
  "approval_decision",
  "tool_execution_completed",
] as const;

/**
 * Opens a Server-Sent Events connection to the backend audit stream.
 * On any security event, debounces 500ms then invalidates all relevant
 * React Query caches so pages refresh automatically.
 *
 * Returns the current connection state string.
 */
export function useSSE(): string {
  const cache = useQueryClient();
  const [state, setState] = useState("connecting");

  useEffect(() => {
    const stream = new EventSource(`${base}/api/v1/events/stream`);
    const seen = new Set<string>();
    let refreshTimer: ReturnType<typeof setTimeout> | undefined;

    stream.onopen  = () => setState("connected");
    stream.onerror = () => setState("reconnecting");

    const update = (event: MessageEvent) => {
      if (!event.lastEventId || seen.has(event.lastEventId)) return;
      seen.add(event.lastEventId);
      if (seen.size > 300) seen.delete(seen.values().next().value as string);
      if (refreshTimer) return;
      refreshTimer = setTimeout(() => {
        refreshTimer = undefined;
        void cache.invalidateQueries({ queryKey: ["events"] });
        void cache.invalidateQueries({ queryKey: ["memories"] });
        void cache.invalidateQueries({ queryKey: ["dashboard-summary"] });
        void cache.invalidateQueries({ queryKey: ["lab-runs"] });
        void cache.invalidateQueries({ queryKey: ["approvals"] });
        void cache.invalidateQueries({ queryKey: ["evaluation-summary"] });
        void cache.invalidateQueries({ queryKey: ["evaluation-metrics"] });
      }, 500);
    };

    SSE_EVENT_TYPES.forEach(type => stream.addEventListener(type, update));

    return () => {
      stream.close();
      if (refreshTimer) clearTimeout(refreshTimer);
      setState("disconnected");
    };
  }, [cache]);

  return state;
}
