# Six-Week Implementation Roadmap

## Overview

This roadmap drives Cybotech from an empty repository to a demonstrable, evaluated AI agent security platform. Each week has a clear objective, split responsibilities, concrete deliverables, acceptance criteria, and the test evidence required to call the week done.

The roadmap is realistic — it reflects a two-person team working intensively over six weeks. It does not assume all features are equally important. See the feature-cut section at the end for a compressed timeline.

---

## Week 1 — Legitimate system

**Objective:** Build a useful, honest support assistant with persistent memory and an audit trail. No attack content yet. This week establishes the foundation everything else depends on.

### Dev A responsibilities

- Implement the assistant workflow: task intake → context retrieval → completion → response
- Create synthetic inbox: fixed email fixtures in `evaluation/datasets/synthetic/emails/`
- Implement `read_email`, `search_documents`, `fetch_customer_record` tool stubs in `infrastructure/sandbox/`
- Implement the OpenAI adapter in `infrastructure/llm/`
- Create synthetic company data: fictional customers, vendor contacts, internal documents
- Write the initial threat model draft

### Dev B responsibilities

- Design the PostgreSQL schema: `sessions`, `documents`, `memories`, `memory_provenance`, `audit_events`
- Write and run the first Alembic migration
- Implement the Memory Guard approve-only path (no quarantine yet): `propose_memory()` → writes trusted record
- Implement provenance capture: source type, trust level, ingestion path
- Define the audit event format and implement `write_event()`
- Define the `MemoryProposal`, `MemoryDecision`, `MemoryProvenance` domain types
- Implement the retrieval service (simple version: no quarantine filter needed yet)
- Implement the FastAPI app factory and `/api/v1/health` endpoint

### Interface contracts agreed this week

- `MemoryProposal` / `MemoryDecision` dataclass shapes
- `AuditEvent` format (event type, payload schema)
- Tool stub return types
- Database session factory signature

### Deliverables

- [ ] `GET /api/v1/health` returns dependency status
- [ ] Assistant reads a synthetic email and searches documents in one task
- [ ] A useful fact (vendor contact) is saved to memory with provenance
- [ ] The same fact is retrieved in a fresh session
- [ ] At least one audit event written per significant step
- [ ] Initial threat model document committed

### Acceptance criteria

A useful fact from a synthetic document survives into a fresh session. Normal assistant work (read email, search docs, fetch record) is fully recorded in the audit trail.

### Test evidence required

- Unit test: `MemoryProposal` with clean provenance is approved by Memory Guard
- Unit test: `write_event()` writes a correctly structured record to `audit_events`
- Integration test: task execution produces expected audit events in sequence
- Integration test: memory written in session A is retrieved in session B

### Risks

- Database schema churn: keep it minimal in week 1; add columns in later migrations
- OpenAI API latency: use `gpt-4o-mini` during development to keep costs and latency low

---

## Week 2 — Demonstrate the vulnerability

**Objective:** Build and document the explicitly vulnerable baseline. Demonstrate at least one reproducible cross-session attack across all three attack objectives.

### Dev A responsibilities

- Create fixed attack emails and documents for all three objectives:
  - Memory poisoning: email claiming new report destination
  - Unauthorized disclosure: document triggering report-send to unapproved address
  - Workflow hijacking: content claiming approval already granted
- Implement the explicit baseline configuration (no Memory Guard evaluation, no gateway checks)
- Implement state reset: delete session/memory/tool-call records from previous run
- Implement run replay: given a case ID and seed, re-run the exact same scenario
- Record model name, provider version, configuration, seed, and actual outcome for every run

### Dev B responsibilities

- Extend audit events to capture: attack case ID, run ID, configuration name
- Add `attack_cases` and `attack_runs` tables to the schema
- Implement `POST /api/v1/attack-runs` and `GET /api/v1/attack-runs/{id}`
- Implement basic `tool_calls` table logging

### Deliverables

- [ ] At least one reproducible cross-session memory poisoning attack documented
- [ ] Unauthorized simulated send achieved in baseline (confirmed via `tool_calls` table)
- [ ] Workflow hijacking demonstrated in baseline
- [ ] All run outcomes recorded honestly — including cases where the attack does not succeed first time
- [ ] State reset verified: running the same case twice produces consistent initial conditions

### Acceptance criteria

At least one documented, reproducible cross-session attack achieves a measurable unauthorized simulated outcome in the baseline configuration. The outcome is verified by querying `memories` and `tool_calls` tables, not by reading model output.

### Test evidence required

- Integration test: attack email ingested → memory with `source_type=email` written to trusted state in baseline
- Integration test: a trusted poisoned memory from session A appears in retrieval in session B in baseline
- Integration test: `send_report` tool call with unapproved destination is recorded in `tool_calls` with `executed=true` in baseline
- State reset test: running the same case twice starts from the same DB state

### Risks

- Model may not follow injection instructions reliably: if so, refine the attack payload phrasing and document the attempt honestly
- Do not fabricate a successful attack; record the actual outcome and iterate on the fixture

---

## Week 3 — Protect persistent memory

**Objective:** Implement Memory Guard's full evaluation and lifecycle. Prove that malicious proposals are quarantined while benign facts remain usable.

### Dev B responsibilities

- Implement provenance-based trust labels: untrusted source → cannot write to high-trust categories
- Implement suspicious-content detection: heuristics for injected instructions, authority claims
- Implement lifecycle states: `quarantined`, `rejected`, `pending` in addition to `trusted`
- Add quarantine audit events: `memory_quarantined`, `memory_rejected`
- Implement retrieval-time status filter: only `trusted`, non-expired, non-revoked records returned
- Add `retrieval_denied` audit event with reason

### Dev A responsibilities

- Run existing attack cases against the `memory_guard` configuration
- Verify that benign cases (legitimate contact updates, ordinary documents) still pass
- Update attack fixtures if needed to reflect new guard behavior
- Write development-set cases 1–20 (10 malicious, 10 benign) in `evaluation/attack_cases/`

### Deliverables

- [ ] Malicious memory proposals quarantined or rejected in `memory_guard` configuration
- [ ] Benign contact updates and document facts still approved and retrievable
- [ ] Quarantined entries absent from retrieval results (verified by query)
- [ ] `retrieval_denied` audit events written for omitted candidates
- [ ] Memory lifecycle states visible in `GET /api/v1/memories`

### Acceptance criteria

In the `memory_guard` configuration: a malicious proposal is quarantined or rejected. A legitimate fact from a trusted source is approved and retrievable in a later session. A retrieval query during the later session does not surface the quarantined entry.

### Test evidence required

- Security invariant test 2: agent cannot write directly to trusted memory
- Security invariant test 3: untrusted email content cannot approve a high-trust memory category
- Security invariant test 7: quarantined record absent from retrieval after status update
- Security invariant test 8: all non-trusted states produce `retrieval_denied` audit event
- Integration test: full attack case → quarantine decision → fresh session retrieval → absence confirmed

### Risks

- Over-blocking: detection heuristics that flag too many benign cases → measure false-positive rate and adjust
- Under-blocking: injection phrasing not covered by heuristics → add cases and iterate; document failures

---

## Week 4 — Protect actions and prove containment

**Objective:** Implement Sentinel AI and the deterministic Policy Enforcement Gateway. Prove that the gateway holds even when Sentinel misses an attack.

### Dev B responsibilities

- Implement structured action proposals: `ActionProposal` dataclass, submission to gateway
- Implement Sentinel AI reviewer: pre-execution model call, `SentinelFinding` output, timeout handling
- Implement Policy Enforcement Gateway with all seven check steps (see `action-enforcement.md`)
- Implement destination allowlist enforcement
- Implement approval requirement checks and approval binding
- Implement memory revocation: `POST /api/v1/memories/{id}/revoke`
- Implement `POST /api/v1/approvals` and `GET /api/v1/approvals/{action_hash}`
- Test Sentinel timeout → deny behavior
- Test gateway failure (DB unavailable) → deny behavior

### Dev A responsibilities

- Implement the detector-miss test case: mock Sentinel to return `PROCEED` for a prohibited action
- Run all three attack objectives against `policy_gateway` and `full` configurations
- Extend run metadata to record: Sentinel finding, gateway decision, approval state
- Complete development-set cases 21–40

### Deliverables

- [ ] Sentinel produces a structured finding before every gateway check (for review-required actions)
- [ ] Gateway blocks unauthorized send in `policy_gateway` and `full` configurations
- [ ] Gateway blocks action even when Sentinel mock returns `PROCEED` (detector-miss case documented)
- [ ] Approval expiry enforced (unit test: expired approval → denied)
- [ ] Approval argument binding enforced (unit test: wrong destination hash → denied)
- [ ] Reviewer timeout → gateway still denies prohibited action

### Acceptance criteria

The Policy Enforcement Gateway blocks prohibited actions even when Sentinel AI does not flag the attack. This must be demonstrated with an explicit detector-miss test case where Sentinel returns `recommendation: PROCEED` but the gateway denies based on the destination allowlist.

### Test evidence required

- Security invariant test 4: all tool calls pass through gateway; no bypass path
- Security invariant test 5: Sentinel finding in audit log before gateway decision (for review-required types)
- Security invariant test 6: Sentinel PROCEED + gateway deny → action blocked
- Security invariant test 10: send_report without approval → gateway denied
- Security invariant test 11 (binding): approval for destination A does not authorize destination B
- Security invariant test 11 (expiry): expired approval → denied
- Security invariant test 14: DB unavailable during policy check → gateway denied
- Security invariant test 15: Sentinel timeout → gateway still enforces correctly

### Risks

- Sentinel model quality: the reviewer model may produce inconsistent findings; document this and ensure gateway independence is proven even with inconsistent Sentinel output
- Approval UX: the two-step approval flow needs to be testable without a real UI; implement a CLI or API-direct approval for the evaluation harness

---

## Week 5 — Evaluation and dashboard

**Objective:** Run the full 60-case evaluation. Build the live security dashboard. Produce an honest, reproducible evaluation report.

### Dev A responsibilities

- Implement the Vibe Hacker bounded runner: execute all 60 cases across all four configurations
- Freeze the holdout evaluation set (20 cases): commit to `evaluation/attack_cases/holdout/`
- Implement programmatic metric calculation from `attack_runs` and `tool_calls` tables
- Execute the full evaluation run and record all outcomes
- Produce `evaluation/reports/committed/week5_results.yaml` with actual measured values

### Dev B responsibilities

- Implement the React dashboard:
  - SSE client (`EventSource` with reconnect)
  - Live event timeline component
  - Memory lifecycle view (status, provenance, decisions)
  - Action review view (Sentinel findings, gateway decisions)
  - Evaluation comparison table
- Implement `GET /api/v1/events` SSE endpoint with replay support
- Implement historical event fetch for reconnect recovery

### Deliverables

- [ ] All 60 cases run across all four configurations
- [ ] Actual metric values recorded (no TBD in the results table)
- [ ] Dashboard displays live attack timeline during an experiment run
- [ ] Dashboard shows memory state transitions and policy decisions
- [ ] SSE reconnect tested: disconnect → reconnect → historical events replayed
- [ ] Dashboard displays imported documents as inert text (no XSS from attack payloads)

### Acceptance criteria

Actual metric measurements are available for all four configurations. The dashboard shows a complete attack timeline from SSE events during a live experiment run. Both defense strengths and remaining failures are visible in the results.

### Test evidence required

- Integration test: SSE disconnect → reconnect → events replayed via `Last-Event-ID`
- Integration test: attack payload in document content rendered as inert text in dashboard (no script execution)
- Evaluation assertion: results file contains programmatically verified outcomes (not model-generated summaries)
- Evaluation assertion: false-positive rate measured from benign case runs

### Risks

- Dashboard scope creep: keep the UI minimal; a working timeline and memory view are sufficient
- Evaluation run time: 60 cases × 4 configurations = 240 runs; at ~30 seconds each this is ~2 hours; plan accordingly
- Model variability: some malicious cases may not succeed consistently in the baseline; record actual rates with variance

---

## Week 6 — Hardening and portfolio release

**Objective:** Make the project demonstrable, reproducible, and honest. Prepare for portfolio review and technical interviews.

### Shared responsibilities

- Fix all integration defects found during week 5 evaluation
- Test a clean checkout: `git clone` → `make setup` → `docker compose up --build` → `make migrate` → working system
- Add startup validation: `scripts/dev/check_env.sh` verifies required environment variables before the backend starts
- Run failure regression cases: DB down, Sentinel timeout, expired approval, revoked memory
- Run security regression cases: all 15 invariant tests must pass
- Record a 3–5 minute demo video covering the complete attack-and-defense scenario
- Update README repository layout to match actual files
- Document known limitations honestly in README §16
- Prepare interview talking points with actual metrics and design rationale

### Dev A — final tasks

- Polish the red-team harness for clean demo execution
- Add a `make demo` target that runs one complete attack scenario end-to-end

### Dev B — final tasks

- Polish the dashboard for demo recording
- Add a downloadable CSV export for evaluation results
- Ensure the frontend displays an informative loading/error state when the backend is unavailable

### Deliverables

- [ ] Clean checkout produces a working local environment from `README.md` instructions alone
- [ ] All 15 security invariant tests pass
- [ ] Evaluation is reproducible: running `make evaluate` produces the same results (within model variance)
- [ ] Architecture, limitations, and evaluation methodology documented accurately
- [ ] 3–5 minute demo recorded and linked from README (or stored in `docs/`)
- [ ] Interview talking points document prepared

### Acceptance criteria

Another developer can follow the README instructions, reproduce the demo, run the evaluation, and understand the security design without additional guidance from the original authors.

### Test evidence required

- Clean-checkout integration test: document the exact commands run and their outcomes
- All 15 `backend/tests/security/test_invariant_*.py` files pass
- Evaluation run produces a results file matching the week 5 baseline (within acceptable model variance)

---

## Integration checkpoints

| End of week | Checkpoint | Owner |
|---|---|---|
| Week 1 | Agent calls Memory Guard `propose_memory()`; audit event recorded | Both |
| Week 2 | Attack case runs against baseline; memory state outcome verified via DB query | Both |
| Week 3 | Memory Guard rejects malicious proposal; retrieval confirms absence | Both |
| Week 4 | Gateway blocks unauthorized send even when Sentinel mock returns PROCEED | Both |
| Week 5 | Dashboard shows complete attack timeline from SSE events | Both |
| Week 6 | Clean checkout + full evaluation reproducible | Both |

---

## Feature cuts for a compressed timeline

If the schedule is 3–4 weeks instead of 6, cut in this order:

| Feature | Decision | Rationale |
|---|---|---|
| Policy Enforcement Gateway | **Keep** | Hardest invariant; most important for portfolio |
| Memory Guard (quarantine + retrieval filter) | **Keep** | Core of the memory security story |
| Audit trail (complete event trace) | **Keep** | Evidence without which nothing else can be demonstrated |
| Baseline attack (one case reproducible) | **Keep** | The "vulnerable" side of the comparison |
| Sentinel AI | **Cut first** | Advisory; gateway must work without it |
| Evaluation engine (automated) | **Cut second** | Replace with manual run + recorded outcomes |
| React dashboard | **Cut or simplify** | Replace with raw audit event log in terminal |
| E2E Playwright tests | **Cut** | Integration tests are sufficient for the demo |
| Full 60-case evaluation | **Reduce** | Run 20 cases (10 malicious, 10 benign) for directional results |

---

## Related documents

- Two-person workflow → [`two-person-workflow.md`](two-person-workflow.md)
- Security invariants → [`../security/security-invariants.md`](../security/security-invariants.md)
- Evaluation methodology → [`../evaluation/methodology.md`](../evaluation/methodology.md)
- Definition of done → README §16
