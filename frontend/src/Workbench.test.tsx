import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AttackLab } from "./Workbench";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("attack lab", () => {
  it("runs and labels a persisted scripted comparison", async () => {
    const report = { run_id: "lab-test", evidence_kind: "scripted", storage: "isolated_sqlite",
      corpus_sha256: "test", metrics: [{ configuration: "full", cases: 1, errors: 0,
        attack_success_rate: 0, attacker_delivery_rate: 0, legitimate_task_success_rate: null,
        mean_latency_ms: 1 }], results: [{ case_id: "memory_poisoning-00", configuration: "full",
        kind: "attack", status: "completed", attack_success: false, legitimate_task_success: null,
        poisoned_memory: false, attacker_delivery: false, memory_states: ["quarantined"], trace: [] }] };
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => ({ ok: true, json: async () => {
      if (init?.method === "POST") return report;
      if (url.includes("lab/cases")) return { items: [{ id: "memory_poisoning-00", kind: "attack",
        objective: "memory_poisoning", split: "development", document: { content: "Synthetic attack" } }] };
      if (url.includes("lab/runs")) return { items: [] };
      return { mode: "offline", reviewer: "deterministic", tools: "simulated" };
    } })));
    const query = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={query}><AttackLab /></QueryClientProvider>);
    await screen.findByText("Synthetic attack");
    await userEvent.click(screen.getByRole("button", { name: "Compare selected scenario" }));
    expect(await screen.findByText("Comparison · SCRIPTED EVIDENCE")).toBeInTheDocument();
    expect(screen.getByText(/memory_poisoning-00 \/ full/)).toHaveTextContent("attack stopped");
  });
});
