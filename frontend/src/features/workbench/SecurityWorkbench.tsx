/**
 * SecurityWorkbench — the advanced research experience.
 *
 * Sub-routes:
 *   /workbench           → Overview
 *   /workbench/attack-lab → Attack Lab
 *   /workbench/evaluations → Evaluations
 *   /workbench/experiments → Experiments (Attack Explorer)
 *   /workbench/memory    → Documents and Memory
 *   /workbench/timeline  → Security Timeline
 */

import { NavLink, Routes, Route, Navigate } from "react-router-dom";
import {
  LayoutDashboard, FlaskConical, BarChart3,
  Search, Database, Clock,
} from "lucide-react";
import { cn } from "@/lib/utils";

import OverviewPage      from "./pages/OverviewPage";
import AttackLabPage     from "./pages/AttackLabPage";
import EvaluationsPage   from "./pages/EvaluationsPage";
import ExperimentsPage   from "./pages/ExperimentsPage";
import MemoryPage        from "./pages/MemoryPage";
import TimelinePage      from "./pages/TimelinePage";

// ---------------------------------------------------------------------------
// Sub-navigation items
// ---------------------------------------------------------------------------
const NAV_ITEMS = [
  { to: "",            end: true,  label: "Overview",              icon: LayoutDashboard },
  { to: "attack-lab",  end: false, label: "Attack Lab",            icon: FlaskConical    },
  { to: "evaluations", end: false, label: "Evaluations",           icon: BarChart3       },
  { to: "experiments", end: false, label: "Experiments",           icon: Search          },
  { to: "memory",      end: false, label: "Documents & Memory",    icon: Database        },
  { to: "timeline",    end: false, label: "Security Timeline",     icon: Clock           },
] as const;

// ---------------------------------------------------------------------------
// Workbench shell
// ---------------------------------------------------------------------------
export default function SecurityWorkbench() {
  return (
    <div className="mx-auto max-w-screen-xl px-4 md:px-8">
      {/* Page title */}
      <div className="pt-7 pb-1">
        <p className="text-xs uppercase tracking-widest text-cyan-400/70">Security Research</p>
        <h1 className="mt-1 text-2xl font-semibold text-slate-100">Security Workbench</h1>
        <p className="mt-1 text-sm text-slate-400">
          Advanced tools for testing, evaluating, and inspecting AI agent security.
        </p>
      </div>

      {/* Sub-navigation */}
      <nav
        aria-label="Workbench navigation"
        className="mt-5 flex gap-0.5 overflow-x-auto border-b border-slate-800 pb-0"
      >
        {NAV_ITEMS.map(({ to, end, label, icon: Icon }) => (
          <NavLink
            key={label}
            to={to}
            end={end}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2.5 text-sm font-medium transition-colors",
                isActive
                  ? "border-cyan-400 text-cyan-200"
                  : "border-transparent text-slate-400 hover:text-slate-200 hover:border-slate-600",
              )
            }
          >
            <Icon className="h-4 w-4 shrink-0" aria-hidden />
            {label}
          </NavLink>
        ))}
      </nav>

      {/* Page content */}
      <div className="py-7">
        <Routes>
          <Route index                   element={<OverviewPage />} />
          <Route path="attack-lab"        element={<AttackLabPage />} />
          <Route path="evaluations"       element={<EvaluationsPage />} />
          <Route path="experiments"       element={<ExperimentsPage />} />
          <Route path="memory"            element={<MemoryPage />} />
          <Route path="timeline"          element={<TimelinePage />} />
          <Route path="*"                 element={<Navigate to="" replace />} />
        </Routes>
      </div>
    </div>
  );
}
