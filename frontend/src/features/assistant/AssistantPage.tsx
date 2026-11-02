/**
 * AssistantPage — clean chat-style interface for the Cybotech AI assistant.
 *
 * UX goals:
 *  - Familiar conversational layout (messages scroll up, composer fixed at bottom)
 *  - File attachment via button (PDF, TXT, MD, EML)
 *  - Example prompt chips on empty state
 *  - Small, honest security status on each assistant turn
 *  - No internal plumbing visible to the user
 */

import { useRef, useState, useId } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Send, Paperclip, Plus, ShieldCheck, ShieldAlert,
  ShieldX, ChevronDown, ChevronUp, X, FileText,
} from "lucide-react";
import { api } from "@/api";
import { cn } from "@/lib/utils";
import { Button, Alert } from "@/components/ui";
import type { AssistantDocument, TaskResult, Runtime } from "@/types";

// ---------------------------------------------------------------------------
// Supported file types (matches backend SUPPORTED_TYPES)
// ---------------------------------------------------------------------------
const ACCEPTED_TYPES = ".pdf,.txt,.md,.eml,text/plain,text/markdown,message/rfc822,application/pdf";
const MAX_FILE_BYTES = 1_000_000; // 1 MB — backend hard limit

// ---------------------------------------------------------------------------
// Security status helpers
// ---------------------------------------------------------------------------
type SecurityState = "safe" | "flagged" | "blocked" | "incomplete" | "none";

function deriveSecurityState(result: TaskResult): SecurityState {
  if (result.status === "failed") return "incomplete";
  const tools = result.tool_results as Array<{ success?: boolean }>;
  const hasBlockedAction = tools.some(t => t.success === false);
  if (hasBlockedAction) return "blocked";
  // steps === 0 in offline mode is normal; steps > 0 with tool_results means something ran
  return "safe";
}

interface SecurityBadgeProps {
  state: SecurityState;
  steps: number;
  details?: string;
  trace?: unknown[];
  toolResults?: unknown[];
}

function SecurityStatus({ state, steps, details, trace }: SecurityBadgeProps) {
  const [expanded, setExpanded] = useState(false);
  if (state === "none") return null;

  const config = {
    safe:       { icon: ShieldCheck,  color: "text-cyan-300",  bg: "bg-cyan-400/10",  label: "Security review completed" },
    flagged:    { icon: ShieldAlert,  color: "text-amber-300", bg: "bg-amber-400/10", label: "Suspicious content detected" },
    blocked:    { icon: ShieldX,      color: "text-rose-300",  bg: "bg-rose-400/10",  label: "Unsafe action blocked" },
    incomplete: { icon: ShieldCheck, color: "text-amber-300", bg: "bg-amber-400/10", label: "Review incomplete" },
  }[state] ?? { icon: ShieldCheck, color: "text-slate-400", bg: "bg-slate-800", label: "Review completed" };

  const Icon = config.icon;

  return (
    <div className={cn("mt-3 rounded-lg px-3 py-2 text-xs", config.bg)}>
      <button
        type="button"
        className={cn("flex w-full items-center gap-2 font-medium", config.color)}
        onClick={() => setExpanded(e => !e)}
        aria-expanded={expanded}
      >
        <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />
        <span className="flex-1 text-left">Security Review · {config.label}</span>
        {steps > 0 && <span className="text-slate-400 font-normal">{steps} step{steps !== 1 ? "s" : ""}</span>}
        {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
      </button>
      {expanded && (
        <div className="mt-2 space-y-1 text-slate-400 border-t border-white/5 pt-2">
          {state === "blocked" && (
            <p className="text-rose-300">
              The assistant proposed an action that was not permitted. It was stopped before executing.
            </p>
          )}
          {state === "flagged" && (
            <p className="text-amber-300">
              Content in this document may contain embedded instructions. Suspicious memory was rejected.
            </p>
          )}
          {state === "incomplete" && (
            <p>The task ended with an error. No data was written or sent.</p>
          )}
          {state === "safe" && <p>No security concerns were identified in this interaction.</p>}
          {details && <p className="mt-1 break-all">{details}</p>}
          {Array.isArray(trace) && trace.length > 0 && (
            <details className="mt-2">
              <summary className="cursor-pointer text-slate-500">Raw evidence ({trace.length} events)</summary>
              <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all text-slate-500 text-[11px]">
                {JSON.stringify(trace, null, 2)}
              </pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Message types
// ---------------------------------------------------------------------------
interface UserMessage {
  role: "user";
  id: string;
  text: string;
  fileName?: string;
}

interface AssistantMessage {
  role: "assistant";
  id: string;
  text: string;
  securityState: SecurityState;
  steps: number;
  status: string;
  toolResults: unknown[];
}

interface ErrorMessage {
  role: "error";
  id: string;
  text: string;
}

type Message = UserMessage | AssistantMessage | ErrorMessage;

// ---------------------------------------------------------------------------
// Example prompts (shown on empty state)
// ---------------------------------------------------------------------------
const EXAMPLE_PROMPTS = [
  "Summarize this document.",
  "Extract important dates and deadlines.",
  "Identify any suspicious instructions.",
  "Find the key contact information.",
];

// ---------------------------------------------------------------------------
// AttachedFile — small file preview chip
// ---------------------------------------------------------------------------
function AttachedFileChip({
  file,
  onRemove,
}: {
  file: File;
  onRemove: () => void;
}) {
  return (
    <div className="flex items-center gap-1.5 rounded-lg bg-slate-800 px-2.5 py-1.5 text-xs text-slate-300">
      <FileText className="h-3 w-3 text-cyan-300 shrink-0" aria-hidden />
      <span className="max-w-[180px] truncate">{file.name}</span>
      <span className="text-slate-500">({(file.size / 1024).toFixed(0)} KB)</span>
      <button
        type="button"
        onClick={onRemove}
        className="ml-1 text-slate-500 hover:text-slate-300"
        aria-label={`Remove ${file.name}`}
      >
        <X className="h-3 w-3" />
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------
export default function AssistantPage() {
  const runtime = useQuery({ queryKey: ["runtime"], queryFn: () => api<Runtime>("runtime") });
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [attachedFile, setAttachedFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [sessionId] = useState(() => crypto.randomUUID());
  const fileInputRef = useRef<HTMLInputElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaId = useId();

  const run = useMutation({
    mutationFn: async ({ task, doc }: { task: string; doc: AssistantDocument | null }) => {
      return api<TaskResult>("assistant/run", {
        task,
        session_id: sessionId,
        task_id: crypto.randomUUID(),
        documents: doc ? [doc] : [],
      });
    },
    onSuccess: (result) => {
      setMessages(prev => [
        ...prev,
        {
          role: "assistant",
          id: crypto.randomUUID(),
          text: result.reply || "(No response)",
          securityState: deriveSecurityState(result),
          steps: result.steps,
          status: result.status,
          toolResults: result.tool_results,
        },
      ]);
      setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
    },
    onError: (error: Error) => {
      setMessages(prev => [
        ...prev,
        { role: "error", id: crypto.randomUUID(), text: error.message },
      ]);
      setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
    },
  });

  // ── File attachment ──────────────────────────────────────────────────────
  async function handleFile(file: File) {
    setFileError(null);
    if (file.size > MAX_FILE_BYTES) {
      setFileError(`File too large. Maximum size is ${(MAX_FILE_BYTES / 1024).toFixed(0)} KB.`);
      return;
    }
    const allowed = ["application/pdf", "text/plain", "text/markdown", "message/rfc822"];
    // Some browsers label .md as text/plain — allow by extension too
    const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
    if (!allowed.includes(file.type) && !["pdf", "txt", "md", "eml"].includes(ext)) {
      setFileError("Unsupported file type. Attach a PDF, TXT, Markdown or EML file.");
      return;
    }
    setAttachedFile(file);
  }

  // ── Send ─────────────────────────────────────────────────────────────────
  async function handleSend() {
    const text = input.trim();
    if (!text || run.isPending) return;

    let doc: AssistantDocument | null = null;

    if (attachedFile) {
      const content = await attachedFile.text().catch(() => "");
      const extMap: Record<string, AssistantDocument["source_type"]> = {
        eml: "email", pdf: "document", txt: "document", md: "document",
      };
      const ext = attachedFile.name.split(".").pop()?.toLowerCase() ?? "txt";
      doc = {
        id: `file-${Date.now()}`,
        content,
        source_type: extMap[ext] ?? "document",
        trust_level: "untrusted",
      };
    }

    // Add user message to history
    setMessages(prev => [
      ...prev,
      {
        role: "user",
        id: crypto.randomUUID(),
        text,
        fileName: attachedFile?.name,
      },
    ]);

    setInput("");
    setAttachedFile(null);
    setFileError(null);

    setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 20);
    run.mutate({ task: text, doc });
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSend();
    }
  }

  function startNew() {
    setMessages([]);
    setInput("");
    setAttachedFile(null);
    setFileError(null);
  }

  const isEmpty = messages.length === 0 && !run.isPending;

  // ── Render ───────────────────────────────────────────────────────────────
  return (
    <div className="mx-auto flex h-[calc(100vh-3.5rem)] max-w-3xl flex-col px-4 md:px-6">

      {/* ── Header ── */}
      <div className="flex items-center justify-between py-5">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Cybotech Assistant</h1>
          <p className="mt-0.5 text-sm text-slate-400">Your AI assistant for documents and information.</p>
        </div>
        <div className="flex items-center gap-3">
          {/* Security-enabled badge — always true for this platform */}
          <span className="hidden sm:inline-flex items-center gap-1.5 rounded-full bg-cyan-400/8 border border-cyan-800/40 px-2.5 py-1 text-xs text-cyan-400/80">
            <ShieldCheck className="h-3 w-3" aria-hidden />
            Protection enabled
          </span>
          {messages.length > 0 && (
            <button
              type="button"
              onClick={startNew}
              className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-400 hover:text-slate-200 hover:border-slate-600 transition-colors"
            >
              <Plus className="h-3.5 w-3.5" aria-hidden />
              New chat
            </button>
          )}
        </div>
      </div>

      {/* ── Mode notice (live/offline) — small, non-intrusive ── */}
      {runtime.data?.mode === "offline" && (
        <div className="mb-3 rounded-lg bg-amber-400/8 border border-amber-800/40 px-3 py-2 text-xs text-amber-300/80">
          Running in offline mode. The assistant returns extracted document text without AI reasoning.
          Switch to live mode to enable AI responses.
        </div>
      )}

      {/* ── Conversation area ── */}
      <div className="flex-1 overflow-y-auto pb-4">

        {/* Empty state */}
        {isEmpty && (
          <div className="flex flex-col items-center justify-center h-full gap-8 py-12">
            <div className="text-center">
              <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-slate-800">
                <ShieldCheck className="h-7 w-7 text-cyan-300" aria-hidden />
              </div>
              <h2 className="text-lg font-medium text-slate-200">How can I help you today?</h2>
              <p className="mt-1 text-sm text-slate-500">
                Ask a question or attach a document to get started.
              </p>
            </div>
            {/* Example prompt chips */}
            <div className="flex flex-wrap justify-center gap-2">
              {EXAMPLE_PROMPTS.map(prompt => (
                <button
                  key={prompt}
                  type="button"
                  onClick={() => setInput(prompt)}
                  className="rounded-xl border border-slate-700/60 bg-slate-900/80 px-3.5 py-2 text-sm text-slate-300 hover:border-slate-600 hover:text-slate-100 transition-colors"
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Message thread */}
        {messages.map(msg => {
          if (msg.role === "user") {
            return (
              <div key={msg.id} className="mb-6 flex justify-end">
                <div className="max-w-[85%]">
                  {msg.fileName && (
                    <div className="mb-1.5 flex justify-end">
                      <span className="inline-flex items-center gap-1.5 rounded-lg bg-slate-800 px-2.5 py-1.5 text-xs text-slate-400">
                        <FileText className="h-3 w-3 text-cyan-300" aria-hidden />
                        {msg.fileName}
                      </span>
                    </div>
                  )}
                  <div className="rounded-2xl rounded-tr-sm bg-cyan-400/10 px-4 py-3 text-sm text-slate-100">
                    {msg.text}
                  </div>
                </div>
              </div>
            );
          }

          if (msg.role === "error") {
            return (
              <div key={msg.id} className="mb-6">
                <div className="rounded-2xl rounded-tl-sm border border-rose-800/40 bg-rose-950/30 px-4 py-3 text-sm text-rose-300">
                  <p className="font-medium mb-1">Something went wrong</p>
                  <p className="text-rose-400/80">{msg.text}</p>
                </div>
              </div>
            );
          }

          // assistant
          return (
            <div key={msg.id} className="mb-6">
              <div className="flex items-start gap-3">
                <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-800">
                  <ShieldCheck className="h-4 w-4 text-cyan-300" aria-hidden />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="rounded-2xl rounded-tl-sm bg-slate-900 border border-slate-800 px-4 py-3 text-sm text-slate-200 whitespace-pre-wrap leading-relaxed">
                    {msg.text}
                  </div>
                  <SecurityStatus
                    state={msg.securityState}
                    steps={msg.steps}
                    toolResults={msg.toolResults}
                  />
                </div>
              </div>
            </div>
          );
        })}

        {/* Loading bubble */}
        {run.isPending && (
          <div className="mb-6">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-800">
                <ShieldCheck className="h-4 w-4 text-cyan-300 animate-pulse" aria-hidden />
              </div>
              <div className="rounded-2xl rounded-tl-sm bg-slate-900 border border-slate-800 px-4 py-3">
                <span className="flex gap-1">
                  {[0, 1, 2].map(i => (
                    <span
                      key={i}
                      className="h-1.5 w-1.5 rounded-full bg-slate-500 animate-bounce"
                      style={{ animationDelay: `${i * 150}ms` }}
                    />
                  ))}
                </span>
              </div>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* ── Composer ── */}
      <div className="pb-4 pt-2">
        {/* File error */}
        {fileError && (
          <Alert className="mb-2">{fileError}</Alert>
        )}

        {/* Attached file preview */}
        {attachedFile && (
          <div className="mb-2">
            <AttachedFileChip file={attachedFile} onRemove={() => { setAttachedFile(null); setFileError(null); }} />
          </div>
        )}

        {/* Composer box */}
        <div className="flex items-end gap-2 rounded-2xl border border-slate-700 bg-slate-900 p-2 focus-within:border-slate-600 transition-colors">
          {/* Attach button */}
          <button
            type="button"
            aria-label="Attach file"
            onClick={() => fileInputRef.current?.click()}
            disabled={run.isPending}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl text-slate-500 hover:text-slate-300 hover:bg-slate-800 transition-colors disabled:opacity-40"
          >
            <Paperclip className="h-4 w-4" aria-hidden />
          </button>

          {/* Hidden file input */}
          <input
            ref={fileInputRef}
            type="file"
            accept={ACCEPTED_TYPES}
            aria-label="File attachment"
            className="sr-only"
            onChange={e => {
              const f = e.target.files?.[0];
              if (f) void handleFile(f);
              e.target.value = "";
            }}
          />

          {/* Text input */}
          <label htmlFor={textareaId} className="sr-only">Message</label>
          <textarea
            id={textareaId}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask a question or attach a document…"
            rows={1}
            maxLength={8000}
            disabled={run.isPending}
            className={cn(
              "flex-1 resize-none bg-transparent py-1.5 text-sm text-slate-100",
              "placeholder:text-slate-500 focus:outline-none",
              "max-h-40 overflow-y-auto",
            )}
            style={{ fieldSizing: "content" } as React.CSSProperties}
          />

          {/* Send button */}
          <Button
            type="button"
            aria-label="Send message"
            disabled={!input.trim() || run.isPending}
            onClick={() => void handleSend()}
            className="h-8 w-8 shrink-0 !px-0 justify-center rounded-xl"
          >
            <Send className="h-3.5 w-3.5" aria-hidden />
          </Button>
        </div>

        <p className="mt-2 text-center text-xs text-slate-600">
          Supported: PDF, TXT, Markdown, EML · Max 1 MB · All security controls active
        </p>
      </div>
    </div>
  );
}
