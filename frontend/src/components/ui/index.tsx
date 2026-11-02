/**
 * Shared primitive UI components.
 * All use the project's Tailwind palette (slate/cyan/rose/amber).
 */

import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Panel — card container
// ---------------------------------------------------------------------------
export function Panel({ className, children, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("rounded-xl border border-slate-800 bg-slate-900/60 p-5", className)}
      {...props}
    >
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Input — text input
// ---------------------------------------------------------------------------
export function Input({ className, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2.5 text-sm",
        "text-slate-100 placeholder:text-slate-500",
        "focus:outline-none focus:ring-2 focus:ring-cyan-400/40",
        className,
      )}
      {...props}
    />
  );
}

// ---------------------------------------------------------------------------
// Textarea
// ---------------------------------------------------------------------------
export function Textarea({ className, ...props }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn(
        "w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2.5 text-sm",
        "text-slate-100 placeholder:text-slate-500 resize-none",
        "focus:outline-none focus:ring-2 focus:ring-cyan-400/40",
        className,
      )}
      {...props}
    />
  );
}

// ---------------------------------------------------------------------------
// Button variants
// ---------------------------------------------------------------------------
type ButtonVariant = "primary" | "ghost" | "danger";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
}

export function Button({ variant = "primary", className, children, ...props }: ButtonProps) {
  return (
    <button
      className={cn(
        "inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition-opacity",
        "disabled:opacity-40 disabled:cursor-not-allowed",
        variant === "primary" && "bg-cyan-300 text-slate-950 hover:bg-cyan-200",
        variant === "ghost"   && "text-cyan-200 hover:text-cyan-100",
        variant === "danger"  && "text-rose-300 hover:text-rose-200",
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Badge
// ---------------------------------------------------------------------------
type BadgeVariant = "neutral" | "success" | "warning" | "danger";

interface BadgeProps {
  variant?: BadgeVariant;
  children: React.ReactNode;
  className?: string;
}

export function Badge({ variant = "neutral", children, className }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-block rounded-full px-2 py-0.5 text-xs font-medium",
        variant === "neutral" && "bg-slate-800 text-slate-300",
        variant === "success" && "bg-cyan-400/10 text-cyan-200",
        variant === "warning" && "bg-amber-400/10 text-amber-200",
        variant === "danger"  && "bg-rose-400/10 text-rose-300",
        className,
      )}
    >
      {children}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Alert — inline error/warning banner
// ---------------------------------------------------------------------------
export function Alert({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <p role="alert" className={cn("text-sm text-rose-300", className)}>
      {children}
    </p>
  );
}

// ---------------------------------------------------------------------------
// Select
// ---------------------------------------------------------------------------
export function Select({ className, ...props }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        "rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100",
        "focus:outline-none focus:ring-2 focus:ring-cyan-400/40",
        className,
      )}
      {...props}
    />
  );
}
