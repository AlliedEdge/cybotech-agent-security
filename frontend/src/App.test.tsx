/**
 * App shell integration tests.
 *
 * These tests render the full <App /> (router + shell + SSE) to verify:
 *   - Top-level navigation renders and routes correctly
 *   - The Security Workbench sub-navigation is reachable
 *   - The SSE status indicator is present
 *
 * Feature-level tests (assistant, attack lab, memory, etc.) live in
 * Workbench.test.tsx where the QueryClient and router are wired in isolation,
 * making mocking simpler and tests faster.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";

// ---------------------------------------------------------------------------
// Minimal fetch stub — satisfies all background queries the App shell fires
// ---------------------------------------------------------------------------
function defaultFetch(url: string): object {
  if (url.includes("runtime"))               return { mode: "offline", reviewer: "deterministic", tools: "simulated" };
  if (url.includes("dashboard/summary"))     return { memory_counts: {}, event_total: 0, event_counts: [] };
  if (url.includes("evaluations/summary"))   return { total_experiment_records: 0, valid_attack_records: 0, valid_benign_records: 0, failed_records: 0, incomplete_records: 0, invalid_records: 0, strata: [] };
  if (url.includes("evaluations/metrics"))   return { items: [] };
  if (url.includes("evaluations/comparability")) return { comparability: { comparable: false, mismatches: [], baseline_count: 0, treatment_count: 0 } };
  if (url.includes("evaluations/experiments")) return { items: [], total: 0 };
  if (url.includes("evaluations/runs"))      return { items: [] };
  if (url.includes("lab/cases"))             return { items: [{ id: "memory_poisoning-00", kind: "attack", objective: "memory_poisoning", split: "development", document: { id: "d1", content: "SYNTHETIC attack", source_type: "email", trust_level: "untrusted" } }] };
  if (url.includes("lab/runs"))              return { items: [] };
  if (url.includes("approvals"))             return { items: [] };
  if (url.includes("security-events"))       return { items: [], limit: 100, offset: 0, total: 0 };
  if (url.includes("memories"))              return { items: [], limit: 100, offset: 0, total: 0 };
  return { items: [] };
}

beforeEach(() => {
  vi.stubGlobal("EventSource", class {
    onopen:  (() => void) | null = null;
    onerror: (() => void) | null = null;
    addEventListener() {}
    close() {}
  });
  vi.stubGlobal("fetch", vi.fn((url: string) =>
    Promise.resolve({ ok: true, status: 200, json: async () => defaultFetch(url) })
  ));
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

// ---------------------------------------------------------------------------
// Navigation shell
// ---------------------------------------------------------------------------
describe("navigation shell", () => {
  it("renders the top nav with Assistant and Security Workbench links", async () => {
    render(<App />);
    const assistantLinks = await screen.findAllByRole("link", { name: /assistant/i });
    const workbenchLinks = screen.getAllByRole("link", { name: /security workbench/i });
    expect(assistantLinks.length).toBeGreaterThan(0);
    expect(workbenchLinks.length).toBeGreaterThan(0);
  });

  it("lands on the Assistant page by default", async () => {
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Cybotech Assistant" })).toBeInTheDocument();
  });

  it("navigates to Security Workbench and shows its heading", async () => {
    render(<App />);
    const links = await screen.findAllByRole("link", { name: /security workbench/i });
    await userEvent.click(links[0]);
    expect(await screen.findByRole("heading", { name: "Security Workbench" })).toBeInTheDocument();
  });

  it("shows all 6 workbench sub-nav tabs after navigating", async () => {
    render(<App />);
    const links = await screen.findAllByRole("link", { name: /security workbench/i });
    await userEvent.click(links[0]);
    await screen.findByRole("heading", { name: "Security Workbench" });

    for (const name of [/overview/i, /attack lab/i, /evaluations/i, /experiments/i, /documents/i, /timeline/i]) {
      expect(screen.getByRole("link", { name })).toBeInTheDocument();
    }
  });

  it("navigates back to Assistant page from the top nav", async () => {
    render(<App />);
    // Go to workbench first
    const wb = await screen.findAllByRole("link", { name: /security workbench/i });
    await userEvent.click(wb[0]);
    await screen.findByRole("heading", { name: "Security Workbench" });
    // Click the Assistant nav link to go back
    const asst = screen.getAllByRole("link", { name: /assistant/i });
    await userEvent.click(asst[0]);
    expect(await screen.findByRole("heading", { name: "Cybotech Assistant" })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// SSE indicator
// ---------------------------------------------------------------------------
describe("SSE status indicator", () => {
  it("shows the live-updates status pill in the top bar", async () => {
    render(<App />);
    // aria-label="Live updates <state>" is on the pill
    expect(await screen.findByLabelText(/live updates/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Security Workbench — persisted data integration
// ---------------------------------------------------------------------------
describe("security workbench data integration", () => {
  it("shows empty state on timeline when no events exist", async () => {
    render(<App />);
    const links = await screen.findAllByRole("link", { name: /security workbench/i });
    await userEvent.click(links[0]);
    await screen.findByRole("heading", { name: "Security Workbench" });
    await userEvent.click(screen.getByRole("link", { name: /timeline/i }));
    expect(await screen.findByText(/no persisted events yet/i)).toBeInTheDocument();
  });

  it("shows empty memory state in the memory page", async () => {
    render(<App />);
    const links = await screen.findAllByRole("link", { name: /security workbench/i });
    await userEvent.click(links[0]);
    await screen.findByRole("heading", { name: "Security Workbench" });
    await userEvent.click(screen.getByRole("link", { name: /documents/i }));
    expect(await screen.findByText("No persisted memories yet.")).toBeInTheDocument();
  });

  it("shows 'unavailable' (not zero) when evaluation metrics have no data", async () => {
    render(<App />);
    const links = await screen.findAllByRole("link", { name: /security workbench/i });
    await userEvent.click(links[0]);
    await screen.findByRole("heading", { name: "Security Workbench" });
    // Metric cards should show — or Unavailable, never hardcoded zero
    await waitFor(() => {
      const unavailable = screen.queryAllByText("Unavailable");
      const dashes = screen.queryAllByText("—");
      expect(unavailable.length + dashes.length).toBeGreaterThan(0);
    });
    expect(screen.queryAllByText("0%").length).toBe(0);
  });
});
