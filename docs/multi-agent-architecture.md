# Multi-Agent Architecture (implemented)

This documents the **bounded, supervised** multi-agent system implemented on
`feat/deployment-readiness`. It is intentionally not described as a distributed
swarm or a self-organizing agent network: it is a single-process LangGraph
coordinator that delegates bounded, typed tasks to independent specialist agents,
with deterministic code as every security-critical boundary.

> Status note: this is an implementation/behavior document. See the bottom for
> what is verified vs. unverified.

## Roles, responsibilities, and forbidden actions

### Supervisor (`agents/supervisor.py` — `SupervisorGraph`)
- Receives a user task, establishes a `correlation_id` (= `task_id`), and routes by
  need. Trivial greetings (short, no documents, no question mark) skip retrieval.
- Delegates bounded tasks to the Primary Assistant, Memory Guard, and Sentinel
  via typed handoffs. Collects and validates their outputs.
- Enforces handoff/step budgets and a workflow deadline (checked between steps).
- **Forbidden:** it does not authorize or execute tools, does not write trusted
  memory, and has no unrestricted access to specialist tools or data. It is **not**
  a subclass or alias of `PrimaryAssistantGraph` (enforced by
  `test_supervisor_is_not_a_subclass_of_primary_assistant_graph`).

### Primary Assistant (`agents/specialists.py` — `PrimaryAssistant`)
- Owns a compiled LangGraph subgraph: `prepare → retrieve_documents →
  retrieve_trusted_memory → reason → finalize`.
- Retrieves documents via the existing RAG, retrieves authorized trusted memory,
  and produces a structured `AgentTurn` (reply, proposed memories, proposed
  actions, done).
- **Forbidden:** it does not execute privileged tools, does not write trusted
  memory, and does not perform security review. Its retrieved evidence stays
  untrusted.

### Memory Guard (`agents/specialists.py` — `MemoryGuardAgent`)
- Owns a compiled LangGraph subgraph (`prepare → review → finalize`).
- Inspects proposed memory updates against bounded source context, flags prompt
  injection / destination-change / policy-override / poisoning, and returns a typed
  `MemoryReviewResult` (`APPROVE`/`REJECT`/`QUARANTINE`/`NEEDS_REVIEW`, risk,
  reasoning, evidence refs, source-trust assessment).
- **Forbidden:** its output is advisory only. It **cannot** promote a memory to
  trusted. Only the deterministic `MemoryGuard` lifecycle service
  (`services/memory_guard/service.py`) performs the actual state transition, and a
  model finding can only add restrictions (quarantine/revoke), never grant trust.

### Sentinel (`agents/specialists.py` — `SentinelAgent`)
- Owns a compiled LangGraph subgraph (`prepare → review → finalize`).
- Receives a typed action proposal and **bounded** untrusted document evidence
  only. Returns a typed `SentinelReviewResult` (security decision, threat category,
  severity, risk level, evidence refs, restrictions, human-review flag).
- **Forbidden:** it cannot authorize or execute tools. The deterministic Policy
  Enforcement Gateway is the final authorization boundary.

### Policy Enforcement Gateway (`services/policy_gateway/service.py`)
- **Not an LLM agent.** Deterministic final authorization for all tool execution.
- Independently validates agent permissions (`require_permission(PROPOSE_ACTION)`),
  tool allowlist, argument schema, trusted/verified memory references, destination
  allowlist, sentinel finding validity + low risk, and `send_report` approval
  binding/consumption. No agent, including the Supervisor, can bypass it. An LLM
  `ALLOW` never authorizes a tool call.

### Vibe Hacker (`services/red_team/graph.py` — `VibeHackerGraph`)
- Independent adversarial agent. Does **not** run on normal user requests.
- Generates synthetic adversarial artifacts (scripted or live-model), validates them
  (must begin with `SYNTHETIC`, synthetic destinations only), and submits them
  through an **isolated** `run_case` that builds a fresh `SupervisorGraph` with real
  security ports in a throwaway database. Baseline profiles disable selected
  defense layers **only** inside that isolated experiment; the Gateway's
  authorization/sandbox boundary stays intact.
- **Forbidden:** it never receives permission to execute real external actions.

## How the Supervisor coordinates them

```
intake (correlation id, route by need)
  → primary (reason + retrieve) → structured proposals
  → if memories: memory_review (Memory Guard agent) → memory lifecycle enforcement
  → if actions: sentinel_review (Sentinel agent) → gateway → sandbox (only if authorized)
  → finalize (status, reply)
```

- Routing by need: a turn with no memories and no actions finalizes without
  invoking reviewers. Trivial greetings additionally skip retrieval.
- Memory review precedes Sentinel review for deterministic, auditable ordering
  (this also keeps recorded model-call order reproducible). Each is an
  independently compiled subgraph invocation, so they remain independent bounded
  execution paths.
- Fail-closed: a missing or failed Sentinel review prevents the gateway from
  authorizing (`review_unavailable`); a failed Memory Guard review quarantines.
- Conflicting recommendations are resolved by deterministic policy (the Gateway),
  not by asking another LLM to choose.

## Typed handoffs (`agents/registry.py`, `agents/contracts.py`)
- `Handoff` carries `handoff_id`, `task_id`, `correlation_id`, `parent_handoff_id`,
  `source`/`destination` agent, `operation`, `trust_label`, `input_reference`/
  `output_reference`, `created_at`/`completed_at`, `failure_reason`, and bounded
  `source_refs`. Routes validate source/destination permissions; circular,
  unauthorized, and cross-role handoffs are rejected
  (`test_unauthorized_handoffs_are_rejected`).
- Review request contracts (`MemoryReviewRequest`, `ActionReviewRequest`) validate
  the handoff destination/operation and that the proposal came from the Primary
  Assistant. Review output contracts (`MemoryReviewResult`, `SentinelReviewResult`)
  are Pydantic-validated before they influence workflow decisions.

## Deterministic vs. LLM operations
- **LLM (advisory/planning):** Primary Assistant reasoning; Memory Guard model
  review; Sentinel model review; Vibe Hacker live generation; optional Supervisor
  planning.
- **Deterministic (authoritative):** every permission check, handoff validation,
  memory lifecycle transition, Gateway authorization, sandbox execution, budgets,
  and routing.

## Provider configuration (`agents/providers.py`)
- OpenAI-compatible and local Ollama-compatible paths via `CYBOTECH_PROVIDER`.
- Per-role models: `CYBOTECH_PRIMARY_MODEL`, `CYBOTECH_MEMORY_MODEL`,
  `CYBOTECH_SENTINEL_MODEL`, `CYBOTECH_ATTACK_MODEL`. Ollama is restricted to a
  loopback HTTP endpoint. No keys/models are hardcoded.
- Structured output is validated against Pydantic schemas (`AgentTurn`,
  `MemoryFinding`, `ModelFinding`) before use. Deterministic offline mode remains
  available for tests.
- Embedding identity and 384-dimension rule are unchanged; Ollama mode uses the
  existing local hash-based embeddings (not semantic Ollama embeddings). Switching
  embedding models requires a reindex plan.

## Observability
- Handoffs, agent invocations, review decisions, gateway decisions, sandbox
  outcomes, and evidence references are persisted as audit events with the task
  `correlation_id`.
- `GET /api/v1/agent-trace/{task_id}` reconstructs a task's agent execution timeline
  from **real persisted records**; the returned `sequence` is derived from recorded
  agent identities, not hardcoded labels. Sensitive action arguments are redacted
  from the trace.
- The Security Workbench has an "Agent Execution Trace" view backed by that API.

## What is verified vs. unverified

**Verified (this session, deterministic/offline):**
- Backend: 335 passed, 5 skipped (2 require disposable PostgreSQL+pgvector; 3 are
  the opt-in live-provider tests). Ruff lint/format and mypy clean.
- Frontend: 7 tests pass; lint, typecheck, and production build succeed.
- New tests prove: Supervisor is not a subclass of PrimaryAssistantGraph; each
  specialist owns a distinct compiled graph and typed I/O; permissions and
  handoffs are enforced; reviews are collected before protected actions and fail
  closed; trivial requests skip unnecessary agents; injection evidence stays
  untrusted; Sentinel cannot authorize; Primary Assistant cannot bypass the
  Gateway; handoff-limit and deadline failures fail closed; the agent-trace API
  reflects real persisted execution.

**Not verified here (requires resources not available in this environment):**
- Disposable **PostgreSQL + pgvector** integration tests (`CYBOTECH_TEST_DATABASE_URL`
  loopback `cybotech_test_*`) — the existing 2 skipped tests were not run; no
  Docker/Postgres was available here.
- **Real provider calls** (OpenAI / Ollama / Qwen) — the opt-in
  `tests/integration/test_live_provider.py` tests were not executed; no API key or
  running Ollama was available. Ollama structured-output compatibility therefore
  remains unverified with a real model.
- The workflow deadline is checked **between steps**; a blocking provider call is
  **not preempted** (this is not claimed otherwise).

**Not in scope for this pass (carried over):**
- Authenticated end-user identities and tenant isolation — the agent registry is
  internal capability, **not** user authentication.
- Durable execution / recovery after process failure.
- Preemptive overall deadline and a combined token/request budget beyond the
  per-step/per-handoff limits implemented here.

## How to verify the unverified items
1. PostgreSQL/pgvector: follow the backend README disposable-DB procedure and run
   `uv run python -m pytest tests/integration/test_postgres_retrieval.py
   tests/integration/test_postgres_document_retrieval.py`.
2. Live providers: `export CYBOTECH_LIVE_PROVIDER_TEST=1` plus provider env, then
   `uv run python -m pytest tests/integration/test_live_provider.py -q -s`. That
   run (and only that run) uses real providers; everything else uses mocks.

---

## Qdrant Vector Database Integration (feat/qdrant-vector-db-migration)

The Primary Assistant's document and memory retrieval paths now route through a
dedicated Qdrant vector database. The multi-agent security architecture is unchanged.

### What changed

| Component | Before | After |
|---|---|---|
| Document retrieval | Python-side cosine ranking (full scan) | Qdrant ANN search → PostgreSQL revalidation |
| Memory retrieval | pgvector SQL cosine_distance + Python re-check | Qdrant ANN search → PostgreSQL revalidation |
| Memory writes | PostgreSQL only | PostgreSQL + Qdrant outbox |
| Trust authority | PostgreSQL | PostgreSQL (unchanged) |

### What did NOT change

- `SupervisorGraph`, `PrimaryAssistant`, `MemoryGuardAgent`, `SentinelAgent` — identical
- `VibeHackerGraph` / red-team lab — uses SQLite isolation, deliberately no Qdrant
- `PolicyGateway` — deterministic, reads from PostgreSQL only, independent of Qdrant
- `DocumentEvidence.trust_label == "untrusted"` — unchanged
- `is_eligible()` trust gate — still enforced in Python against live PostgreSQL state
- Memory lifecycle state machine — unchanged
- Handoff contracts and typed outputs — unchanged
- Audit events — unchanged

### Security invariants after migration

1. **PostgreSQL is the sole authority on memory trust.** Qdrant metadata cannot grant trust.
2. **Qdrant search results are IDs only.** Canonical records are always re-fetched from PostgreSQL.
3. **Session isolation is enforced in PostgreSQL.** The `session_id` WHERE clause in the
   revalidation query is the authoritative scope boundary — not the Qdrant payload filter.
4. **Qdrant downtime does not corrupt data.** The transactional outbox retains pending events;
   writes are never falsely reported as indexed.
5. **The Policy Gateway reads from PostgreSQL only.** It never reads Qdrant payload for
   authorization decisions.

### Retrieval architecture

```
Primary Assistant calls document_retrieval.retrieve(query, session_id)
  → DocumentService._retrieve_via_qdrant()
      → embedder.embed(query)                           [generates query vector]
      → vector_store.search_document_chunks(...)        [Qdrant ANN, session_id filter]
      → SELECT ... WHERE chunk_id IN (...) AND session_id = ?   [PostgreSQL revalidation]
      → returns DocumentEvidence (trust_label="untrusted")

Primary Assistant calls retrieval.retrieve(RetrievalQuery)
  → RetrievalService._retrieve_via_qdrant()
      → embedder.embed(query)                           [generates query vector]
      → vector_store.search_memories(...)               [Qdrant ANN, model filter]
      → SELECT ... WHERE id IN (...) AND status = trusted     [PostgreSQL pre-filter]
      → is_eligible(m) for each candidate               [canonical trust gate]
      → returns Memory records (status==trusted, not revoked, not expired)
```

### How to verify Qdrant integration

```bash
# Verify Qdrant integration tests (requires live Qdrant):
export CYBOTECH_TEST_QDRANT_URL=http://localhost:6333
export CYBOTECH_TEST_QDRANT_API_KEY=<key>
cd backend && uv run python -m pytest tests/integration/test_qdrant_integration.py \
  tests/integration/test_qdrant_document_retrieval.py \
  tests/integration/test_qdrant_memory_retrieval.py -v
```
