import { useRef, useState } from "react";
import { FileText, Paperclip, Send, ShieldCheck, Sparkles } from "lucide-react";
import { api } from "./api";

type Event = { type: string; outcome?: string | null; reason_code?: string | null; payload?: { status?: string; count?: number } };
type Message = { role: "user" | "assistant"; text: string; file?: string; events?: Event[]; reviewed?: boolean };
type Result = { status: string; reply: string };
const types: Record<string, string> = { pdf: "application/pdf", txt: "text/plain", md: "text/markdown", eml: "message/rfc822" };

export function currentSession() {
  const existing = sessionStorage.getItem("cybotech-session");
  if (existing) return existing;
  const id = crypto.randomUUID();
  sessionStorage.setItem("cybotech-session", id);
  return id;
}

export function AssistantChat() {
  const [session] = useState(currentSession);
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  async function index(name: string, contentType: string, bytes: Uint8Array) {
    if (bytes.length > 1_000_000) throw new Error("Documents must be 1 MB or smaller.");
    let binary = "";
    for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
    await api("documents/ingest", { title: name, content_type: contentType, content_base64: btoa(binary), session_id: session });
  }

  async function send(event: React.FormEvent) {
    event.preventDefault();
    if (!text.trim() || busy) return;
    const request = text.trim(); const attachment = file;
    setBusy(true); setError("");
    try {
      if (attachment) {
        const extension = attachment.name.split(".").pop()?.toLowerCase() ?? "";
        if (!types[extension]) throw new Error("Choose a PDF, TXT, MD, or EML document.");
        if (attachment.size > 1_000_000) throw new Error("Documents must be 1 MB or smaller.");
        await index(attachment.name, types[extension], new Uint8Array(await attachment.arrayBuffer()));
      }
      let task = request;
      if (request.length > 8000) {
        await index("Pasted text.txt", "text/plain", new TextEncoder().encode(request));
        task = "Summarize the pasted text and answer based on its contents.";
      }
      const taskId = crypto.randomUUID();
      setMessages(current => [...current, { role: "user", text: request, file: attachment?.name }]);
      setText(""); setFile(null); if (fileInput.current) fileInput.current.value = "";
      const result = await api<Result>("assistant/run", { task, session_id: session, task_id: taskId, documents: [] });
      let events: Event[] = []; let reviewed = false;
      try { const log = await api<{ items: Event[] }>(`security-events?correlation_id=${encodeURIComponent(taskId)}&limit=100`); events = log.items; reviewed = true; } catch { /* Do not imply that missing evidence means a clean review. */ }
      setMessages(current => [...current, { role: "assistant", text: result.reply || `Request ${result.status}.`, events, reviewed: reviewed && result.status === "completed" }]);
    } catch (err) { setText(request); setError(err instanceof Error ? err.message : "The request could not be completed."); }
    finally { setBusy(false); }
  }
  return <div className="mx-auto flex min-h-[calc(100vh-9rem)] w-full max-w-4xl flex-col">
    <div className="flex-1 space-y-6 py-5" aria-live="polite">
      {!messages.length && <div className="mx-auto max-w-xl pt-16 text-center"><div className="mx-auto mb-6 flex h-14 w-14 items-center justify-center rounded-2xl bg-cyan-300 text-slate-950"><Sparkles size={25} /></div><h2 className="text-3xl font-semibold tracking-tight">How can I help?</h2><p className="mt-3 text-sm leading-6 text-slate-400">Ask a question or attach a document. Cybotech reviews the information and actions used in your session.</p><p className="mt-3 text-xs text-slate-500">Local synthetic demo · responses may be extractive in offline mode.</p></div>}
      {messages.map((message, i) => <article key={i} className={message.role === "user" ? "ml-auto max-w-[85%] rounded-2xl bg-slate-800 px-5 py-4" : "max-w-[95%] px-1 py-3"}>
        <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">{message.role === "user" ? "You" : "Cybotech"}</p><p className="whitespace-pre-wrap break-words text-sm leading-7 text-slate-100">{message.text}</p>{message.file && <p className="mt-3 text-xs text-slate-400"><FileText size={14} className="mr-1 inline" />{message.file}</p>}
        {message.role === "assistant" && <SecurityDetails events={message.events ?? []} reviewed={!!message.reviewed} />}
      </article>)}
      {busy && <p role="status" className="text-sm text-slate-400">Working on your request…</p>}
    </div>
    <form onSubmit={send} className="sticky bottom-4 rounded-2xl border border-slate-700 bg-slate-900 p-3 shadow-xl">
      {file && <div className="flex items-center gap-2 px-2 pb-2 text-xs text-cyan-200"><FileText size={14} />{file.name}<button type="button" className="ml-2 text-slate-400" onClick={() => { setFile(null); if (fileInput.current) fileInput.current.value = ""; }}>Remove</button></div>}
      <label htmlFor="chat-input" className="sr-only">Ask anything or describe what you need</label><textarea id="chat-input" rows={3} value={text} onChange={e => setText(e.target.value)} placeholder="Ask anything or describe what you need..." className="w-full resize-y bg-transparent px-2 py-2 text-sm text-slate-100 outline-none placeholder:text-slate-500" />
      {text.trim().length > 8000 && <p className="px-2 pb-2 text-xs text-slate-400">Long text will be indexed as a document and summarized.</p>}
      <div className="flex items-center justify-between"><div><input ref={fileInput} type="file" accept=".pdf,.txt,.md,.eml" className="sr-only" id="attach-document" onChange={e => setFile(e.target.files?.[0] ?? null)} /><button type="button" onClick={() => fileInput.current?.click()} className="flex items-center gap-2 rounded-lg px-2 py-2 text-xs text-slate-300 hover:bg-slate-800"><Paperclip size={16} /> Attach document</button></div><button disabled={busy || !text.trim()} className="flex items-center gap-2 rounded-xl bg-cyan-300 px-4 py-2 text-sm font-medium text-slate-950 disabled:opacity-40"><Send size={15} /> Send</button></div>
      {error && <p role="alert" className="px-2 pt-2 text-sm text-rose-300">{error}</p>}
    </form><p className="pb-3 text-center text-xs text-slate-500">Use synthetic documents only. Messages stay visible while this page is open; previous messages are not automatically included in new requests.</p>
  </div>;
}

function SecurityDetails({ events, reviewed }: { events: Event[]; reviewed: boolean }) {
  const blocked = events.some(e => e.type === "gateway_denied" || (e.type === "gateway_decision" && e.outcome === "denied"));
  const suspicious = events.some(e => e.type === "memory_proposal_invalid" || ["quarantined", "rejected"].includes(e.outcome ?? e.payload?.status ?? ""));
  const untrusted = events.some(e => e.type === "document_retrieval_completed" && (e.payload?.count ?? 0) > 0);
  const status = !reviewed ? "Security details unavailable" : blocked ? "Action blocked" : suspicious ? "Suspicious content detected" : untrusted ? "Document treated as untrusted" : "Protected · no alerts reported";
  return <details className="mt-4 text-xs text-slate-400"><summary className="cursor-pointer"><ShieldCheck size={14} className="mr-1 inline" />{status} · View Security Details</summary><div className="mt-3 rounded-lg border border-slate-800 p-3">{!reviewed ? "Security events could not be loaded." : events.length ? events.map((e, i) => <p key={i} className="py-1">{e.type.replaceAll("_", " ")} · {e.outcome ?? "recorded"}{e.reason_code ? ` · ${e.reason_code}` : ""}</p>) : "No task-specific security events were recorded."}<p className="mt-2 text-slate-500">These indicators describe recorded checks, not a guarantee that content is safe.</p></div></details>;
}
