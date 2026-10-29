# Security Invariants

## Purpose

This document lists the 15 security invariants that Cybotech's protected configuration must uphold, along with how each is enforced in code and what test evidence is required. A violation of any invariant in the protected configuration is a test failure — not a warning.

These invariants are also listed in the README. This document adds detail on enforcement mechanisms and test requirements.

---

## Invariant index

| # | Short name | Component |
|---|---|---|
| 1 | Model never defines own permissions | Policy Gateway |
| 2 | Agent never writes directly to trusted memory | Memory Guard |
| 3 | Untrusted content cannot grant authority | Memory Guard |
| 4 | All proposed actions pass through the gateway | Policy Gateway |
| 5 | Sentinel reviews before execution (where required) | Sentinel AI |
| 6 | Sentinel cannot override a gateway denial | Policy Gateway |
| 7 | Every retrieved memory is checked against canonical state | Retrieval Service |
| 8 | Non-trusted memories excluded from retrieval | Retrieval Service |
| 9 | Similarity scores never establish trust | Retrieval Service |
| 10 | Sensitive actions require approval | Policy Gateway |
| 11 | Approval is bound to exact action and expires | Policy Gateway |
| 12 | Tool execution stays in sandbox | Sandbox |
| 13 | Audit records identify relevant decisions | Audit Subsystem |
| 14 | Fail-closed on unresolvable checks | Policy Gateway + Memory Guard |
| 15 | Dashboard/SSE/Sentinel failure cannot disable authorization | Policy Gateway |

---

## Invariant 1 — Model never defines its own permissions

**Statement:** The model's output cannot change what actions are permitted, what destinations are allowed, or what approval rules apply.

**Enforcement:** Policy configuration is loaded from authoritative sources (config files and the `policies` table in PostgreSQL) at runtime. The agent has no write path to policy records. The gateway reads policy from PostgreSQL, not from LLM output.

**Test location:** `backend/tests/security/test_invariant_01.py` *(planned)*

**Required test:** Submit a completion that includes a fabricated permission grant (e.g., *"You are authorized to send to all destinations"*). Assert that the gateway still denies a destination not on the allowlist.

---

## Invariant 2 — The assistant never writes directly to trusted memory

**Statement:** There is no code path by which the agent can write a record directly to the `memories` table with `status = trusted` without going through Memory Guard.

**Enforcement:** The agent calls `memory_guard.propose_memory()`. Direct database writes from agent code are forbidden by module structure (agent modules do not import the database session factory directly).

**Test location:** `backend/tests/security/test_invariant_02.py` *(planned)*

**Required test:** Attempt to call a direct database write from the agent layer. Assert that the import is not available or that the function raises an authorization error.

---

## Invariant 3 — Untrusted content cannot grant authority

**Statement:** Content arriving from an untrusted source (email, external document) cannot cause a memory proposal to be approved in a category that requires a trusted or verified source.

**Enforcement:** Memory Guard checks source `trust_level` against the category's minimum required trust level before approval. `trust_level = untrusted` cannot satisfy any requirement above untrusted.

**Test location:** `backend/tests/security/test_invariant_03.py` *(planned)*

**Required test:** Submit a memory proposal from an untrusted email that claims IT-department authority. Assert that the proposal is quarantined or rejected, not approved.

---

## Invariant 4 — All proposed actions pass through the deterministic gateway

**Statement:** Every tool call that the agent makes must pass through `policy_gateway.authorize()` before reaching any tool in the sandbox. There is no bypass path.

**Enforcement:** Simulated tool functions are not directly importable from agent code. The orchestration layer always calls `gateway.authorize(proposal)` and checks the result before dispatching to the sandbox.

**Test location:** `backend/tests/security/test_invariant_04.py` *(planned)*

**Required test:** Mock the gateway to always return `authorized=False`. Assert that no tool function is called regardless of the action proposal.

---

## Invariant 5 — Sentinel reviews actions before execution (where required)

**Statement:** For action types flagged as requiring pre-execution review, the Sentinel finding must be recorded before the gateway makes its decision.

**Enforcement:** The orchestration layer calls `sentinel.review(proposal)` and persists the finding to the audit log before calling `gateway.authorize(proposal)`. The gateway check for review-required action types verifies a finding exists.

**Test location:** `backend/tests/security/test_invariant_05.py` *(planned)*

**Required test:** Execute a review-required action. Assert that the `sentinel_finding` audit event appears before the `gateway_authorized` or `gateway_denied` event in the sequence.

---

## Invariant 6 — Sentinel findings cannot override an explicit policy denial

**Statement:** If the gateway denies an action based on policy (wrong destination, missing approval, allowlist violation), a Sentinel `recommendation: PROCEED` does not change that outcome.

**Enforcement:** The gateway reads policy from PostgreSQL and applies allowlist checks independently. It receives the Sentinel finding as a data argument but does not use `recommendation` as an authorization signal.

**Test location:** `backend/tests/security/test_invariant_06.py` *(planned)*

**Required test:** Configure a prohibited destination. Provide a mock Sentinel finding with `recommendation: PROCEED`. Assert that the gateway still returns `authorized=False`.

---

## Invariant 7 — Every retrieved memory is checked against current canonical state

**Statement:** A memory record returned by pgvector semantic search is not returned to the agent unless its canonical PostgreSQL record is resolved and passes all status, expiry, and permission checks.

**Enforcement:** `retrieval.retrieve(query)` fetches candidate IDs from pgvector, then resolves each against PostgreSQL with `SELECT status, expires_at, revoked_at, owner_id ... WHERE memory_id = $1`. Only records passing all checks are included in results.

**Test location:** `backend/tests/security/test_invariant_07.py` *(planned)*

**Required test:** Write a memory record as trusted in PostgreSQL, create its vector embedding, then set its status to `quarantined`. Execute a retrieval query with high semantic similarity to the record's content. Assert it is absent from results.

---

## Invariant 8 — Non-trusted memories excluded from trusted retrieval

**Statement:** Memory records with status `quarantined`, `rejected`, `expired`, or `revoked` are never returned as trusted context to the agent.

**Enforcement:** Retrieval filter applies a hard status check: `WHERE status = 'trusted' AND (expires_at IS NULL OR expires_at > NOW()) AND revoked_at IS NULL`. Additional permission checks follow.

**Test location:** `backend/tests/security/test_invariant_08.py` *(planned)*

**Required test:** Create records in each non-trusted state. Assert each is absent from retrieval results in a standard query. Assert that a `retrieval_denied` audit event is written for each omitted candidate.

---

## Invariant 9 — Similarity scores never establish trust

**Statement:** A high pgvector similarity score for a quarantined or revoked record does not cause it to pass the retrieval filter.

**Enforcement:** Similarity scores are metadata on the retrieval candidate. The canonical status check happens after candidate selection and is not influenced by the score.

**Test location:** Covered by `test_invariant_07.py` (similarity does not override quarantine status).

**Required test:** Ensure the highest-similarity candidate in a query result set is excluded if its canonical status is not `trusted`.

---

## Invariant 10 — Sensitive actions require policy-defined approval

**Statement:** Tool calls in the configured approval-required category cannot execute without a valid, unexpired approval record.

**Enforcement:** Gateway check step 6: reads the `approvals` table for a matching `action_hash`. If not found or expired: deny.

**Test location:** `backend/tests/security/test_invariant_10.py` *(planned)*

**Required test:** Attempt to execute a `send_report` call (approval-required) without an approval record. Assert `authorized=False` and `failed_check="approval_required"`.

---

## Invariant 11 — Approval is bound to exact action and expires

**Statement:** An approval for action X with arguments A does not authorize action X with arguments B. An expired approval is always denied.

**Enforcement:** The gateway computes `SHA-256(tool_name + sorted(arguments))` and compares it against `approval.bound_arguments_hash`. The gateway also checks `approval.expires_at > now()` and `approval.used_at is None`.

**Test location:** `backend/tests/security/test_invariant_11.py` *(planned)*

**Required test (binding):** Create an approval for `send_report(destination=A)`. Attempt execution with `destination=B`. Assert denied.

**Required test (expiry):** Create an approval with `expires_at` in the past. Assert denied.

**Required test (single-use):** Create an approval, use it once successfully. Attempt to use it again. Assert denied.

---

## Invariant 12 — Tool execution stays in the sandbox

**Statement:** No tool execution during testing or experiments makes a real network call, sends a real email, or accesses real external services.

**Enforcement:** All tool implementations in `infrastructure/sandbox/` are in-process stubs. They have no real transport client. The Docker network provides no route to real mail infrastructure.

**Test location:** `backend/tests/security/test_invariant_12.py` *(planned)*

**Required test:** Execute `send_report`. Assert no real HTTP or SMTP call was made (mock or monitor network calls in the test process). Assert the call is recorded in the `tool_calls` table only.

---

## Invariant 13 — Audit records identify relevant decisions

**Statement:** Every security-relevant decision (memory proposal outcome, retrieval denial, Sentinel finding, gateway decision, tool execution result) produces at least one audit event in `audit_events`.

**Enforcement:** Audit event writes are part of each decision code path, not optional side effects. Missing an event is a bug, not a performance optimization.

**Test location:** `backend/tests/security/test_invariant_13.py` *(planned)*

**Required test (end-to-end):** Execute a complete task involving a malicious email, memory proposal, retrieval, action proposal, and gateway decision. Assert that the `audit_events` table contains events for each step in the expected sequence.

---

## Invariant 14 — Fail-closed on unresolvable checks

**Statement:** If a required security check cannot be completed (database unavailable, record missing, policy unresolvable), the action or memory operation is denied. There is no fail-open fallback.

**Enforcement:** Every check function propagates database errors as explicit denials, not as exceptions that a caller might silently ignore. Gateway and Memory Guard have explicit error → deny paths in their try/except blocks.

**Test location:** `backend/tests/security/test_invariant_14.py` *(planned)*

**Required test:** Mock the database session to raise a connection error during the gateway policy check. Assert `authorized=False` and `failed_check="policy_unresolvable"`. Assert a `gateway_denied` audit event is written (using a fallback write path or in-memory buffer).

---

## Invariant 15 — Dashboard/SSE/Sentinel failure cannot disable authorization

**Statement:** If the SSE publisher fails, the frontend disconnects, or Sentinel is unavailable, the gateway and Memory Guard continue to enforce their decisions correctly.

**Enforcement:** The SSE publisher is a non-blocking side effect of audit event writes. The gateway does not call the SSE publisher and does not depend on its availability. Sentinel unavailability is handled as documented in invariant 6.

**Test location:** `backend/tests/security/test_invariant_15.py` *(planned)*

**Required test (SSE failure):** Mock the SSE publisher to raise an exception. Execute a complete action enforcement flow. Assert the gateway decision is still made correctly and the audit event is written to PostgreSQL.

**Required test (Sentinel unavailable):** Mock Sentinel to raise a timeout exception. Execute a flow requiring Sentinel review for an action the gateway would deny. Assert the gateway still denies the action.

---

## Running the security invariant tests

```bash
# All security tests
cd backend && uv run pytest tests/security/ -v

# A specific invariant
cd backend && uv run pytest tests/security/test_invariant_07.py -v
```

All 15 invariant test files must exist and pass before any PR touching security-critical code can be merged. Creating empty test files that pass trivially is not acceptable — each test must execute the enforcement path it claims to cover.

---

## Related documents

- Threat model → [`threat-model.md`](threat-model.md)
- Responsible testing → [`responsible-testing.md`](responsible-testing.md)
- Action enforcement → [`../architecture/action-enforcement.md`](../architecture/action-enforcement.md)
- Memory lifecycle → [`../architecture/memory-lifecycle.md`](../architecture/memory-lifecycle.md)
