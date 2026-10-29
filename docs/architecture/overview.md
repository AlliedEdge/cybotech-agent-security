# Architecture Overview

## Purpose

This document describes the logical structure of Cybotech: its components, how a request flows through the system, the boundaries that separate concerns, and the rules that govern how modules depend on each other.

Cybotech is a **modular monolith**. All components run in a single FastAPI process. They are separated by module boundaries and interfaces, not by network boundaries or separate deployment units. This is an intentional choice for the MVP: it keeps the system simple to reason about, easy to run locally, and straightforward to debug without distributed tracing.

---

## Logical components

| Component | Module path | Role |
|---|---|---|
| API Layer | `cybotech.api.v1` | HTTP and SSE entry points; input validation; routing |
| Primary Assistant Agent | `cybotech.agents` | Task interpretation; context assembly; proposal generation |
| Model Provider Adapter | `cybotech.infrastructure.llm` | OpenAI SDK wrapper behind a replaceable interface |
| Memory Guard | `cybotech.services.memory_guard` | Intercepts all memory writes; assigns lifecycle states |
| Retrieval Service | `cybotech.services.retrieval` | Semantic search + trust revalidation before returning context |
| Sentinel AI | `cybotech.services.sentinel` | Pre-execution advisory review of proposed actions |
| Policy Enforcement Gateway | `cybotech.services.policy_gateway` | Deterministic final authorization for all tool execution |
| Orchestration Service | `cybotech.services.orchestration` | Coordinates the agent workflow steps |
| Red-Team Harness (Vibe Hacker) | `cybotech.services.red_team` | Controlled attack execution and outcome recording |
| Evaluation Engine | `cybotech.services.evaluation` | Metric calculation across configurations |
| Audit & Event Subsystem | `cybotech.infrastructure.events` | Structured event writing and SSE publishing |
| Database Infrastructure | `cybotech.infrastructure.database` | SQLAlchemy engine and session factory |
| Embedding Infrastructure | `cybotech.infrastructure.embeddings` | pgvector adapter for semantic retrieval |
| Simulated Tools (Sandbox) | `cybotech.infrastructure.sandbox` | In-process stubs for all agent-callable tools |
| Domain Models | `cybotech.domain` | Pure data types and interfaces for memories, policies, attacks, audit |
| Schemas | `cybotech.schemas` | Pydantic v2 request/response models for the API layer |

---

## Request flow — legitimate support task

```
Employee (browser)
  │
  ▼
API Layer (/api/v1/sessions/{id}/tasks)
  │  Validates input with Pydantic v2
  ▼
Orchestration Service
  │  Coordinates the assistant workflow
  ▼
Primary Assistant Agent
  ├─► Model Provider Adapter ──► OpenAI API
  │     (completions, context assembly)
  │
  ├─► Retrieval Service
  │     ├─► pgvector (candidate search)
  │     └─► PostgreSQL (canonical record validation)
  │         Returns only trusted, non-expired, permitted records
  │
  ├─► Memory Guard (on memory write proposals)
  │     └─► PostgreSQL (persist with lifecycle state)
  │
  └─► Sentinel AI (on action proposals)
        └─► Policy Enforcement Gateway (final authorization)
              └─► Simulated Tool (if authorized)

All paths write to:
  Audit & Event Subsystem ──► PostgreSQL (audit_events)
                           └─► SSE Publisher ──► Browser dashboard
```

---

## Trust boundaries

### Boundary 1 — External content / application code

Everything that enters the system as document or email content is **untrusted**. It is processed by the agent but must not influence policy, security decisions, or authorization state directly.

- Incoming email bodies: untrusted
- Ingested documents: untrusted
- LLM-generated output (completions, proposals): untrusted until authorized

### Boundary 2 — Agent proposals / Memory Guard and Policy Gateway

The agent proposes memory writes and tool actions. It does not execute them directly.

- Memory writes cross the Memory Guard boundary before touching PostgreSQL
- Tool calls cross the Policy Enforcement Gateway boundary before reaching any tool
- The agent has no direct write path to trusted memory or tool execution

### Boundary 3 — Policy configuration / runtime state

Policy rules (allowlists, approval requirements, tool permissions) are loaded from authoritative configuration outside the agent's editable context. The agent cannot modify them.

### Boundary 4 — Simulated sandbox / host environment

Simulated tools are in-process stubs. They have no real network access, cannot send real email, and cannot reach real external services. This boundary is enforced by design (no real transport configured) and by the tool allowlist in the gateway.

---

## Module dependency rules

These rules prevent circular dependencies and keep security components independent:

1. `domain/` has no dependencies on any other Cybotech module. It contains pure types and interfaces only.
2. `schemas/` depends only on `domain/`.
3. `infrastructure/` depends on `domain/` but not on `services/` or `agents/`.
4. `services/` depends on `domain/` and `infrastructure/` but not on `api/` or `agents/`.
5. `agents/` depends on `services/` and `infrastructure/` but not on `api/`.
6. `api/` depends on `services/`, `agents/`, and `schemas/`.
7. `services/policy_gateway/` does not import from `services/sentinel/`. The gateway makes independent decisions; it receives the Sentinel finding as a data argument, it does not call Sentinel directly.
8. `services/sentinel/` does not call `services/policy_gateway/`. Sentinel produces findings; it does not authorize.

---

## Dependency directions

```
api/
 └── agents/
      └── services/ (memory_guard, retrieval, sentinel, policy_gateway, orchestration)
           └── infrastructure/ (database, embeddings, llm, events, sandbox)
                └── domain/
```

No arrows point upward. `domain/` is the foundation with no upward dependencies.

---

## Key design decisions

| Decision | Rationale |
|---|---|
| Modular monolith over microservices | Simpler to reason about security boundaries; no distributed auth complexity in MVP |
| Explicit workflow steps over an orchestration framework | Easier to audit, test, and instrument each step; no hidden agent loops |
| One model provider (OpenAI), behind an adapter | Single integration point; adapter is replaceable without changing agent logic |
| PostgreSQL + pgvector only (no Qdrant) | One storage system; canonical state and vectors co-located; simpler consistency story |
| Fail-closed everywhere | Any unresolvable security check results in denial, not permissive default |
| Sentinel is advisory, gateway is final | Model-based review can be wrong; deterministic code is the last line of defense |

---

## What is not in this document

- Memory lifecycle states and transitions → [`memory-lifecycle.md`](memory-lifecycle.md)
- Action review and gateway authorization flow → [`action-enforcement.md`](action-enforcement.md)
- Local deployment and Docker Compose setup → [`deployment.md`](deployment.md)
- Threat model and attacker assumptions → [`../security/threat-model.md`](../security/threat-model.md)
- Security invariants and their tests → [`../security/security-invariants.md`](../security/security-invariants.md)
- API contract → [`../api/api-contract.md`](../api/api-contract.md)
