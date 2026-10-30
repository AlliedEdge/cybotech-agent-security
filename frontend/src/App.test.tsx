import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";

const response = (body: unknown, ok = true) => ({ ok, status: ok ? 200 : 503, json: async () => body });

describe("security dashboard", () => {
  beforeEach(() => {
    vi.stubGlobal("EventSource", class { addEventListener() {} close() {} });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.includes("security-events")) return response({ items: [] });
      if (url.includes("memories")) return response({ items: [] });
      return response({}, false);
    }));
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("shows persisted-data empty states", async () => {
    render(<App />);
    expect(await screen.findByText("No persisted events yet.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Memories" }));
    expect(await screen.findByText("No persisted memories yet.")).toBeInTheDocument();
  });

  it("labels denied actions as denials", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => url.includes("security-events")
      ? response({ items: [{ id: "evt-1", type: "gateway_decision", timestamp: "2026-01-01T00:00:00Z", actor: "gateway", outcome: "denied", reason_code: "destination_not_approved" }] })
      : response({ items: [] })));
    render(<App />);
    expect(await screen.findByText("destination_not_approved")).toBeInTheDocument();
    expect(screen.getByText("denied")).toBeInTheDocument();
  });

  it("uses complete server summaries when the event page is partial", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.includes("dashboard/summary")) return response({
        memory_counts: { trusted: 45, pending: 0, quarantined: 0, revoked: 0, rejected: 0, expired: 0 },
        event_total: 450,
        event_counts: [],
      });
      if (url.includes("security-events")) return response({ items: [], limit: 100, offset: 0, total: 450 });
      return response({ items: [], limit: 100, offset: 0, total: 0 });
    }));
    render(<App />);
    expect(await screen.findByText("45")).toBeInTheDocument();
    expect(screen.getByText("450")).toBeInTheDocument();
  });

  it("requires confirmation and reports a failed memory action accurately", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") return response({}, false);
      if (url.includes("security-events")) return response({ items: [] });
      return response({ items: [{ id: "m1", content: "Synthetic fact", category: "fact", status: "quarantined", source_type: "email", source_id: "email-1", created_at: null, reason: "untrusted_source" }] });
    });
    vi.stubGlobal("fetch", fetchMock);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Memories" }));
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    expect(confirm).toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalledWith(expect.stringContaining("/approve"), expect.anything());
    confirm.mockReturnValue(true);
    vi.spyOn(window, "alert").mockImplementation(() => {});
    await user.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(window.alert).toHaveBeenCalled());
  });
});
