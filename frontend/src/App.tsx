/**
 * Cybotech — Root application component.
 *
 * Sets up:
 * - TanStack Query provider (for API data fetching)
 * - React Router (for navigation between dashboard views)
 * - Application shell
 *
 * Status: foundation scaffold.
 * Actual routing and feature views are planned for Week 5.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// QueryClient with sensible defaults for a security dashboard:
// - No automatic background refetching (explicit refresh only)
// - Retry failed requests once before surfacing an error
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,       // 30s — data stays fresh
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppShell />
    </QueryClientProvider>
  );
}

/**
 * AppShell renders the top-level layout.
 *
 * Planned for Week 5:
 * - Replace with BrowserRouter + route definitions
 * - Add layout shell with sidebar navigation
 * - Wire up feature views (Overview, Memories, Attacks, Audit, Evaluations)
 */
function AppShell() {
  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center">
      <div className="text-center max-w-lg px-6">
        <h1 className="text-3xl font-bold text-gray-900 mb-2">Cybotech</h1>
        <p className="text-gray-500 mb-1">Agent Memory Security &amp; Red-Team Platform</p>
        <p className="text-sm text-gray-400 mb-6">Detect. Defend. Verify.</p>
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-amber-100 text-amber-700 text-xs font-medium">
          <span className="w-2 h-2 rounded-full bg-amber-500 animate-pulse" />
          Architecture &amp; foundation — implementation in progress
        </div>
        <p className="mt-6 text-xs text-gray-400">
          Dashboard UI is planned for Week 5. Backend API is available at{" "}
          <code className="font-mono">/api/v1/health</code>.
        </p>
      </div>
    </div>
  );
}
