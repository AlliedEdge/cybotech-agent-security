# Two-Person Workflow

## Purpose

This document defines how the two-person team collaborates on Cybotech: ownership boundaries, shared interfaces, integration checkpoints, code review expectations, and branch conventions. The goal is to keep both developers unblocked and avoid either person becoming a bottleneck.

---

## Ownership model

Ownership means: you are responsible for the design, implementation, and tests of this component. You are also the primary reviewer for changes to it by the other developer.

### Developer A — Agent and attack engineering

| Component | Module path | Week introduced |
|---|---|---|
| Primary Assistant Agent | `backend/src/cybotech/agents/` | Week 1 |
| Simulated tools (sandbox stubs) | `backend/src/cybotech/infrastructure/sandbox/` | Week 1 |
| Model provider adapter | `backend/src/cybotech/infrastructure/llm/` | Week 1 |
| Synthetic inbox and document fixtures | `evaluation/datasets/synthetic/` | Week 1 |
| Attack case fixtures | `evaluation/attack_cases/` | Week 2 |
| Red-team harness (Vibe Hacker) | `backend/src/cybotech/services/red_team/` | Week 2 |
| Baseline configuration | Configuration/env files | Week 2 |
| Evaluation case execution | `backend/src/cybotech/services/evaluation/` (run execution) | Week 5 |

### Developer B — Security and observability

| Component | Module path | Week introduced |
|---|---|---|
| Memory Guard | `backend/src/cybotech/services/memory_guard/` | Week 1 |
| Retrieval service | `backend/src/cybotech/services/retrieval/` | Week 1 |
| Domain types and interfaces | `backend/src/cybotech/domain/` | Week 1 |
| Database schema and migrations | `backend/migrations/` | Week 1 |
| Audit event subsystem | `backend/src/cybotech/infrastructure/events/` | Week 1 |
| FastAPI app factory and API layer | `backend/src/cybotech/api/` | Week 1 |
| Sentinel AI reviewer | `backend/src/cybotech/services/sentinel/` | Week 4 |
| Policy Enforcement Gateway | `backend/src/cybotech/services/policy_gateway/` | Week 4 |
| Approval mechanism | `backend/src/cybotech/domain/policies/` | Week 4 |
| Security dashboard (React frontend) | `frontend/` | Week 5 |
| SSE publisher and event stream endpoint | `backend/src/cybotech/infrastructure/events/` | Week 5 |
| Evaluation reporting and metric calculation | `backend/src/cybotech/services/evaluation/` (metrics) | Week 5 |

### Shared ownership

| Area | Both developers |
|---|---|
| Threat model | Co-authored; both maintain |
| Interface contracts | Agreed in week 1; updated by mutual consent |
| Integration tests | Both write; both review |
| Security invariant tests | Both write for their respective components |
| Code review for security-critical code | Both must approve |
| `pyproject.toml` and dependency additions | Both must agree |
| Final demo and presentation preparation | Both |

---

## Interface contracts

These contracts are agreed in week 1 and are the handoff points between the two developers. Both sides commit to not changing a contract without agreeing on the change together.

### Contract 1 — `MemoryProposal` → `MemoryDecision`

**Owner:** Dev B defines; Dev A consumes

```python
# Defined in backend/src/cybotech/domain/memories/
@dataclass
class MemoryProposal:
    content: str
    source_document_id: str
    proposed_by: str
    proposed_at: datetime
    claimed_category: str
    raw_context: str

@dataclass
class MemoryDecision:
    proposal_id: str
    status: MemoryStatus          # trusted | quarantined | rejected | pending
    reason: str
    decided_at: datetime
```

### Contract 2 — `RetrievalQuery` → `list[MemoryRecord]`

**Owner:** Dev B defines; Dev A consumes

```python
@dataclass
class RetrievalQuery:
    query_text: str
    session_id: str
    max_results: int = 5
    min_similarity: float = 0.7

@dataclass
class MemoryRecord:
    id: str
    content: str
    trust_label: str
    source_type: str
    retrieved_at: datetime
```

### Contract 3 — `ActionProposal` → `GatewayDecision`

**Owner:** Dev B defines; Dev A consumes

```python
@dataclass
class ActionProposal:
    tool_name: str
    arguments: dict[str, Any]
    proposed_by: str
    session_id: str
    task_id: str
    user_task_description: str
    retrieved_memory_ids: list[str]
    proposed_at: datetime

@dataclass
class GatewayDecision:
    authorized: bool
    reason: str
    failed_check: str | None
    approval_id: str | None
    decided_at: datetime
```

### Contract 4 — Tool stub interface

**Owner:** Dev A defines; Dev B integrates via sandbox dispatch

```python
# Each tool stub implements:
class ToolStub(Protocol):
    def execute(self, arguments: dict[str, Any]) -> ToolResult: ...

@dataclass
class ToolResult:
    tool_name: str
    arguments: dict[str, Any]
    success: bool
    result: dict[str, Any] | None
    error: str | None
    executed_at: datetime
```

### Contract 5 — `AuditEvent` format

**Owner:** Dev B defines; all components consume

```python
@dataclass
class AuditEvent:
    id: str                         # ULID
    event_type: str                 # see SSE event types in api-contract.md
    session_id: str | None
    run_id: str | None
    timestamp: datetime
    actor: str                      # component that generated the event
    payload: dict[str, Any]
    sequence_number: int            # monotonically increasing per run
```

---

## Integration checkpoints

Each checkpoint is a defined handoff where both developers verify that their independently built components work together.

### Checkpoint 1 — End of Week 1

**Test:** Dev A's assistant calls Dev B's `memory_guard.propose_memory()` with a clean provenance. An audit event is written.

**Pass criteria:**
- `MemoryDecision` returned with `status = trusted`
- `audit_events` table contains `memory_approved` event
- Memory is retrievable in a fresh session via `retrieval.retrieve()`

**Responsible:** Both run together; either can call "pass"

---

### Checkpoint 2 — End of Week 2

**Test:** Dev A's attack case (malicious email) runs against Dev B's baseline configuration. Memory state and tool call outcome verified.

**Pass criteria:**
- `memories` table contains a trusted record with `source_type = email` (baseline: no guard)
- `tool_calls` table contains a `send_report` call with unapproved destination and `executed = true`
- Run metadata recorded with model name, seed, and configuration

**Responsible:** Both verify the database state

---

### Checkpoint 3 — End of Week 3

**Test:** Same malicious email case runs against `memory_guard` configuration.

**Pass criteria:**
- `memories` table: record has `status = quarantined` or `rejected`
- Retrieval in a subsequent session returns 0 results for a query matching the poisoned content
- `retrieval_denied` audit event present

**Responsible:** Both verify DB state and audit log

---

### Checkpoint 4 — End of Week 4

**Test:** Detector-miss case: Dev A's attack → Sentinel mock returns `PROCEED` → Dev B's gateway still denies.

**Pass criteria:**
- `tool_calls` record has `authorized = false`
- `policy_decisions` record has `failed_check = destination_not_allowed`
- `gateway_denied` audit event present
- No `tool_execution_completed` event in the trace

**Responsible:** Both review the audit event sequence

---

### Checkpoint 5 — End of Week 5

**Test:** Live attack scenario runs; Dev B's dashboard shows the complete timeline.

**Pass criteria:**
- SSE events appear in the dashboard in real time during the run
- Memory quarantine, gateway denial, and audit events all visible
- Dashboard reconnect tested (manual: close browser tab, reopen, verify history replayed)

**Responsible:** Both present the running demo to each other

---

## Code review expectations

### For all PRs

- At least one review from the other team member before merge
- CI must pass (lint, typecheck, tests)
- PR description includes: what changed, what was tested, what was not tested

### For security-critical PRs

Security-critical modules: `memory_guard/`, `policy_gateway/`, `sentinel/`, `retrieval/`, `domain/policies/`, `infrastructure/database/`

- **Both team members must approve** before merge
- PR description must include: what security invariant(s) this change affects, how failure was tested, what the failure mode is if this code has a bug

### Review turnaround

- Aim to review within one working day
- If blocked on a review, flag in the team channel — do not let reviews stall for more than two days
- Do not merge a security-critical PR without review, even under time pressure

---

## Branch conventions

```
feat/dev-a/short-description     # Dev A feature branch
feat/dev-b/short-description     # Dev B feature branch
fix/short-description            # bug fix (either developer)
security/short-description       # security fix (always requires both reviewers)
test/short-description           # test additions
docs/short-description           # documentation only
chore/short-description          # tooling, deps, CI
```

Prefix branches with `dev-a/` or `dev-b/` during active parallel development to make it clear whose work is in progress. Drop the prefix after week 3 when the main integration is complete.

---

## Avoiding bottlenecks

### Mock-first development

In weeks 1–3, use interface mocks to unblock parallel work:

- Dev A mocks the Memory Guard to return `MemoryDecision(status=trusted)` while Dev B implements the real guard
- Dev B mocks the agent to submit a canned `ActionProposal` while Dev A implements the real agent
- Replace mocks with real implementations at the integration checkpoint

### Stub the database layer

Dev A should be able to run agent unit tests without a live database. Use SQLite or an in-memory mock for unit tests. Integration tests use the real PostgreSQL container.

### Don't wait — document the decision and move on

If an interface decision is unclear, make the minimal reasonable choice, document it with a `# DECISION: ...` comment, and tell the other developer. Do not block on non-critical decisions.

---

## Communication and planning

- Daily async update: what you're working on, any blockers
- Weekly sync (30 minutes): integration checkpoint demo, next week plan
- Shared task board (GitHub Projects or similar): one card per deliverable from this roadmap
- All architectural decisions → `docs/architecture/` or ADR format in `docs/development/`

---

## Related documents

- Six-week roadmap → [`roadmap.md`](roadmap.md)
- Interface contracts (domain types) → `backend/src/cybotech/domain/`
- Security invariant tests → `backend/tests/security/`
- Contributing guide → [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md)
