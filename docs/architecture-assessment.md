# Architecture Assessment — Multi-Agent Evolution

> Status: pre-implementation audit of `feat/deployment-readiness`. This document
> records what genuinely exists today versus what is a label or wrapper, the
> security boundaries that must not weaken, and the phased plan. All claims are
> grounded in file:line references verified by direct code reading.

## 1. Existing agents and their actual responsibilities

| Component | File | Genuinely independent agent? | Real behavior |
|---|---|---|---|
| **PrimaryAssistantGraph** | `agents/graph.py:61` | No — a single bounded StateGraph that inline-calls a model plus deterministic services | Owns the entire assistant workflow: `prepare_request → retrieve_documents → retrieve_trusted_memory → reason → (memory_agent_review → memory_guard) and/or (sentinel_review → policy_gateway → sandbox_execution) → finalize` (`graph.py:103-150`). Only `_reason` calls an LLM (`graph.py:185`, `ModelProvider.generate`). |
| **SupervisorGraph** | `agents/supervisor.py:9` | **No — it is a subclass alias, not an orchestrator** | `class SupervisorGraph(PrimaryAssistantGraph): pass`. Adds nothing. The "supervisor" identity is only attached inside `_reason` as `parent_agent=SUPERVISOR` (`graph.py:189`) and on emitted handoffs (`source=SUPERVISOR`), but execution is the primary graph doing everything inline. |
| **VibeHackerGraph** | `services/red_team/graph.py` | **Yes** — genuine independent StateGraph | 8 nodes (`intake→select_scenario→plan→generate_payload→validate_payload→submit_attack→collect_evidence→finalize`). Supports `scripted` and `live_model`. `submit` defaults to `run_case`, which builds a fresh `SupervisorGraph` with real security ports as the experiment victim (`red_team/lab.py:210-222`). |
| **Memory Guard (model)** | `services/memory_guard/model.py` | Partial — has its own model call but runs as a node, not a compiled workflow | `ModelMemoryReviewer.analyze` → `MemoryFinding{suspicious, rationale}` via `chat.completions.parse`. Invoked from `_memory_agent_review` (`graph.py:284`), not an independently compiled graph. |
| **Memory Guard (lifecycle)** | `services/memory_guard/service.py` | No — a deterministic service, correctly NOT an LLM agent | `MemoryGuard.propose_memory`/`transition` is the only component that can write trusted memory. `model_suspicious` can only quarantine/revoke, never trust (`service.py:118-122`, `transition` requires `review_verified`). |
| **Sentinel (model)** | `services/sentinel/model.py` | Partial — own model call, runs as a node, not a compiled workflow | `ModelSentinel.review_with_context` → `ModelFinding{risk, rationale, suspicious}` → `SentinelFinding{status, risk, rationale, evidence_refs, disposition, provider}`. Invoked from `_sentinel_review` (`graph.py:330`). |
| **Sentinel (rules)** | `services/sentinel/service.py` | No — deterministic reviewer | Regex-based advisory review; never authorizes. |
| **Policy Gateway** | `services/policy_gateway/service.py` | Correctly NOT an agent — deterministic final authorization | `PolicyGateway._check` validates tool schema, principal, `require_permission(PROPOSE_ACTION)`, trusted memory references, arguments, destination allowlist, sentinel validity+low-risk, send_report approval binding. The only authorizer. |
| **Retrieval / RAG** | `services/retrieval/service.py`, `services/documents` | No — deterministic services | pgvector `cosine_distance` candidate search + canonical-state revalidation; DocumentService chunks+embeds. Untrusted evidence only. |

## 2. Existing LangGraph graphs and how they interact

There are exactly **two compiled `StateGraph`s**, with no subgraph composition between them:

1. **PrimaryAssistantGraph** (`graph.py:61-150`): one graph, 10 nodes. Routing is conditional after `reason` (memories→memory path, actions→sentinel path, else next turn/finalize) and after `gateway` (authorized→sandbox, else next action/turn/finalize). Memory review and Sentinel review are *sequential*, not concurrent.
2. **VibeHackerGraph** (`red_team/graph.py`): independent graph. It calls `run_case` (a function), which constructs a **new** `SupervisorGraph` instance with lab-only `DisabledGateway`/`DisabledGuard`/`DisabledSentinel` for baseline profiles (`red_team/lab.py:139-180`). This is genuine isolation — the attack path reuses the same underlying defense graph but in a throwaway sqlite DB.

Coupling note: Sentinel's finding reaches the Gateway via a side-channel cache (`SentinelPortAdapter.findings` keyed by `action_hash`, popped in `PolicyGatewayPort.authorize` — `services/integration/ports.py`). It is not carried as a typed handoff to the gateway; this coupling must be made explicit and tamper-resistant.

## 3. Current handoff and orchestration limitations

- **Supervisor is a label, not an orchestrator.** `SupervisorGraph(PrimaryAssistantGraph)` adds no delegation. There is no component that decides *which* specialist agents are needed and dispatches *bounded* tasks to *independently compiled* agent workflows.
- **Specialist reviews are inline nodes, not independent agents.** Memory Guard and Sentinel run as nodes *inside* the primary graph. They lack their own compiled LangGraph execution path, dedicated system prompt is held in the model adapter (not the agent contract), and their outputs are weakly typed (Memory Guard: a `bool`; Sentinel: a `dict`/`SentinelFinding`).
- **No routing by need.** Every request passes through `retrieve_documents → retrieve_trusted_memory → reason`. A greeting still triggers RAG and memory retrieval. There is no short-circuit for trivial tasks.
- **Reviews are sequential**, not concurrent, even when Memory Guard and Sentinel operate on independent inputs.
- **Handoff schema is incomplete.** `Handoff` (`registry.py`) lacks `input_reference`, `output_reference`, `created_at`, `completed_at`, `failure_reason`. There is no payload hash/size cap beyond `source_refs` max_length=50 and field-level string caps.
- **No queryable task execution tree.** Handoffs are written as `AuditEvent` rows (`event_type="agent_handoff"`); there is no dedicated API reconstructing a task's agent timeline as a tree. The frontend would have to infer it.
- **Deadline is non-preemptive.** `_check_deadline` runs between steps (`graph.py:_check_deadline`); a blocking provider call cannot be interrupted. No durable execution, no combined token/request budget, no bounded retries beyond `max_retries=0` on the OpenAI client.
- **Model review output does not influence memory trust directly** (correct), but the *output contract* is too thin for the target (no APPROVE/REJECT/QUARANTINE/NEEDS_REVIEW decision, no risk class, no evidence refs, no source-trust assessment from Memory Guard; no threat category/severity/risk-score/restrictions/human-review from Sentinel).

## 4. Security boundaries that must NOT be weakened

1. **Gateway is the sole final authorizer.** `PolicyGateway._check` rejects unknown tools, disallowed principals, untrusted memory refs, invalid arguments, non-allowlisted destinations, missing/invalid/expired/consumed approvals, and any sentinel finding that is not `completed`+valid+`low` risk (`policy_gateway/service.py`). An LLM `ALLOW` must never authorize a tool call.
2. **Only the deterministic memory lifecycle service promotes memory to trusted.** `model_suspicious` can quarantine/revoke, never trust. `transition(..., trusted, ...)` requires `review_verified` and a valid state-machine transition (`memory_guard/service.py`). Model findings may add restrictions only.
3. **Retrieved documents are untrusted evidence.** `DocumentEvidence.trust_label` is the literal `"untrusted"`; provenance is supplied by the application resolver, never derived from content (`memory_guard/service.py:_source_context`).
4. **Sandbox isolation.** `SandboxDispatcher` has no real transport. Lab-only `Disabled*` defenses exist *only* inside the isolated sqlite experiment in `run_case`, never on live user traffic.
5. **Least-privilege agent identities.** `require_permission` gates `PROPOSE_MEMORY` (Memory Guard), `PROPOSE_ACTION` (Gateway), and `REVIEW_MEMORY`/`REVIEW_ACTION` (handoff validation). Review agents cannot obtain the primary's retrieval/proposal permissions (`registry.py`).
6. **Fail-closed defaults.** Reviewer unavailable → Gateway denies (`review_unavailable`); model sentinel unavailable → `flag_for_denial`; memory review unavailable → quarantine; no silent fallback from live provider to a scripted double.

## 5. Phased implementation plan

Aligned with the task's required phases. Each phase runs its relevant tests before continuing.

1. **Agent contract & registry improvements** (`agents/contracts.py`, `agents/registry.py`): strengthen `Handoff` (add `input_reference`, `output_reference`, `created_at`, `completed_at`, `failure_reason`; cap payload hashes/refs); add typed review *output* contracts — `MemoryReviewResult` (decision APPROVE/REJECT/QUARANTINE/NEEDS_REVIEW, risk, reasoning, evidence_refs, source_trust) and `SentinelReviewResult` (security_decision, threat_category, severity, risk_level, evidence_refs, restrictions, human_review). Preserve existing `MemoryReviewRequest`/`ActionReviewRequest`.
2. **Independent specialist-agent workflows** (`agents/memory_guard_agent.py`, `agents/sentinel_agent.py` new): compile Memory Guard and Sentinel as their own LangGraph subgraphs with dedicated system prompts, typed I/O, role-specific provider config (`providers.py`), bounded context, per-call timeout, structured error/timeout handling, and execution metadata. Keep the deterministic lifecycle/gateway as the enforcement layer behind them.
3. **Real Supervisor orchestration** (`agents/supervisor.py` rewrite, new `agents/supervisor_graph.py`): `SupervisorGraph` becomes a genuine orchestrator StateGraph that is NOT a subclass of `PrimaryAssistantGraph`. It establishes a correlation ID, plans (deterministically; LLM planning optional), routes by need (trivial requests skip RAG/reviews/red-team), delegates bounded tasks to specialist subgraphs via typed handoffs, runs Memory Guard + Sentinel concurrently when their inputs are ready and independent, collects/validates results, resolves conflicts by explicit deterministic policy, and coordinates final response generation. Primary Assistant becomes the reasoning+retrieval specialist.
4. **Reliability & security enforcement**: explicit limits (max handoffs, steps, model calls, per-provider timeout, overall deadline, token/context budgets); fail-closed handling for provider-unavailable, invalid structured output, timeout, handoff validation failure, gateway denial, persistence failure, graph interruption; bounded safe retries only; honest documentation that the deadline is non-preemptive for blocking calls unless cancellation is actually implemented.
5. **Persistence & observability**: persist the task execution tree (handoffs with parent links, agent status, provider/model ids, structured review decisions, gateway decisions, sandbox outcomes, evidence refs) from real execution; add a dedicated Agent Execution Trace API that reconstructs a task's agent timeline from persisted records (not hardcoded labels); reduce audit payloads to references/hashes/redacted summaries (partially done already — `argument_keys`, `error_present`).
6. **Frontend integration**: normal Assistant stays a simple chat with a small security-outcome indicator (no graph internals). Security Workbench gains an Agent Execution Trace view driven by the new API; Vibe Hacker controls remain Workbench-only.
7. **PostgreSQL/pgvector & provider integration tests**: add opt-in integration tests with disposable pgvector (Docker) and an opt-in real-provider (Ollama/OpenAI) test; clearly report which tests used mocks vs real calls. Do not weaken or rewrite existing tests.
8. **Documentation & final regression**: update architecture docs (agent roles/forbidden actions, Supervisor coordination, handoffs, deterministic-vs-LLM ops, Gateway enforcement, Vibe Hacker isolation, provider config, verified-vs-unverified). Run full backend+frontend suites, lint, format, type checks, and production build.

### Non-goals / what will NOT be done

- Will not introduce real email sending, shell execution, arbitrary network access, or external tools.
- Will not add end-user authentication/tenant isolation in this pass (the task lists it as remaining; the registry is internal capability, not auth — this will be documented, not silently claimed as auth).
- Will not claim preemption of blocking provider calls unless real cancellation is implemented.
- Will not switch embedding models/dimensions (384, current identity) without a reindex plan.
