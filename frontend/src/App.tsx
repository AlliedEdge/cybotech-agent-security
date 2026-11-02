import { BrowserRouter, Routes, Route, NavLink, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ShieldCheck, MessageSquare, FlaskConical } from "lucide-react";
import { cn } from "@/lib/utils";
import { useSSE } from "@/hooks/useSSE";
import AssistantPage from "@/features/assistant/AssistantPage";
import SecurityWorkbench from "@/features/workbench/SecurityWorkbench";

// ---------------------------------------------------------------------------
// QueryClient — created once at root level
// ---------------------------------------------------------------------------
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 15_000, retry: 1, refetchOnWindowFocus: false },
  },
});

// ---------------------------------------------------------------------------
// App root
// ---------------------------------------------------------------------------
export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Shell />
      </BrowserRouter>
    </QueryClientProvider>
  );
}

// ---------------------------------------------------------------------------
// Shell — top-level layout with two-item nav
// ---------------------------------------------------------------------------
function Shell() {
  const sseState = useSSE();

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      {/* ── Top navigation bar ── */}
      <header className="border-b border-slate-800 bg-slate-950/90 backdrop-blur sticky top-0 z-40">
        <div className="mx-auto flex h-14 max-w-screen-xl items-center gap-6 px-4 md:px-8">
          {/* Logo */}
          <div className="flex items-center gap-2 mr-4">
            <ShieldCheck className="h-5 w-5 text-cyan-300" aria-hidden />
            <span className="font-bold text-slate-100 tracking-tight">CYBOTECH</span>
          </div>

          {/* Primary navigation */}
          <nav aria-label="Main navigation" className="flex gap-1">
            <TopNavLink to="/" icon={<MessageSquare className="h-4 w-4" />} label="Assistant" />
            <TopNavLink to="/workbench" icon={<FlaskConical className="h-4 w-4" />} label="Security Workbench" />
          </nav>

          {/* SSE status — small, unobtrusive */}
          <div className="ml-auto">
            <span
              aria-label={`Live updates ${sseState}`}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs",
                sseState === "connected"
                  ? "border-cyan-800/60 text-cyan-400/70"
                  : "border-slate-700 text-slate-500",
              )}
            >
              <span
                className={cn(
                  "h-1.5 w-1.5 rounded-full",
                  sseState === "connected" ? "bg-cyan-400" : "bg-slate-600",
                )}
              />
              {sseState === "connected" ? "Live" : sseState}
            </span>
          </div>
        </div>
      </header>

      {/* ── Page content ── */}
      <main className="flex-1">
        <Routes>
          <Route path="/"           element={<AssistantPage />} />
          <Route path="/workbench/*" element={<SecurityWorkbench />} />
          <Route path="*"           element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

// ---------------------------------------------------------------------------
// TopNavLink
// ---------------------------------------------------------------------------
function TopNavLink({
  to,
  icon,
  label,
}: {
  to: string;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <NavLink
      to={to}
      end={to === "/"}
      className={({ isActive }) =>
        cn(
          "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors",
          isActive
            ? "bg-cyan-400/10 text-cyan-200"
            : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/60",
        )
      }
    >
      {icon}
      {label}
    </NavLink>
  );
}
