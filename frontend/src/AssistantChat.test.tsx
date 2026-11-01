import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AssistantChat } from "./AssistantChat";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); sessionStorage.clear(); });

it("sends a message and displays an action denial from its security events", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, json: async () => url.includes("security-events")
    ? { items: [{ type: "gateway_decision", outcome: "denied" }] }
    : { status: "completed", reply: "The action was blocked." } })));
  const user = userEvent.setup();
  render(<AssistantChat />);
  await user.type(screen.getByLabelText("Ask anything or describe what you need"), "Send the report");
  await user.click(screen.getByRole("button", { name: "Send" }));
  expect(await screen.findByText("The action was blocked.")).toBeInTheDocument();
  expect(screen.getByText(/Action blocked/)).toBeInTheDocument();
});

it("does not claim a clean review if security events cannot be loaded", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: !url.includes("security-events"), status: 503, json: async () => ({ status: "completed", reply: "Done." }) })));
  const user = userEvent.setup();
  render(<AssistantChat />);
  await user.type(screen.getByLabelText("Ask anything or describe what you need"), "Hello");
  await user.click(screen.getByRole("button", { name: "Send" }));
  expect(await screen.findByText(/Security details unavailable/)).toBeInTheDocument();
});
