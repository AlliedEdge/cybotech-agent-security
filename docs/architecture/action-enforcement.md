# Action Enforcement

## Purpose

This document describes how proposed tool actions are reviewed by Sentinel AI and authorized by the Policy Enforcement Gateway before any tool execution occurs. It covers the proposal interface, Sentinel's advisory role, gateway authorization checks, approval binding, simulated execution, auditing, and failure handling.

---

## The problem this solves

An agent that can propose tool calls based on its context can be manipulated into proposing the wrong call if its context has been shaped by injected content. The assistant might:

- Propose sending a report to an attacker-controlled destination
- Propose skipping an approval step based on a fabricated authority claim
- Propose a tool call unrelated to the user's actual task

No amount of careful prompt engineering makes this impossible. The defense must sit in application code, not in the model.

---

## Action proposal interface

The agent produces a structured action proposal. It does not call tools directly:

```python
@dataclass
class ActionProposal:
    tool_name: str                  # must be on the gateway allowlist
    arguments: dict[str, Any]       # typed and validated by the gateway
    proposed_by: str                # agent identifier
    session_id: str
    task_id: str                    # links back to the original user task
    user_task_description: str      # what the user actually asked for
    retrieved_memory_ids: list[str] # which memory records influenced this proposal
    proposed_at: datetime
```

The gateway validates the schema of `arguments` independently of the model's intent. A well-formed but unauthorized call is still denied.

---

## Sentinel AI — pre-execution review

Sentinel is a read-only, advisory reviewer. It sees the proposed action before the gateway makes its authorization decision. It cannot authorize or deny execution; only the gateway can do that.

### What Sentinel evaluates

- Does the proposed action match the user's stated task?
- Does the destination or target look suspicious?
- Are any arguments inconsistent with the retrieved memory context?
- Does the action appear to be disclosing sensitive data to an unexpected destination?
- Does anything in the retrieved memory used to justify this action look like injected content?

### Sentinel output

```python
@dataclass
class SentinelFinding:
    risk_level: RiskLevel           # LOW | MEDIUM | HIGH | CRITICAL
    flags: list[str]                # e.g. ["destination_mismatch", "injected_context"]
    rationale: str                  # concise explanation
    recommendation: Recommendation  # PROCEED | ESCALATE | DENY
    reviewed_at: datetime
    model_name: str                 # which model produced this finding
```

The `SentinelFinding` is:

1. Persisted to PostgreSQL as an audit event
2. Passed to the gateway as informational context
3. **Never used as the sole basis for authorization**

A `recommendation: PROCEED` from Sentinel does not authorize the action. The gateway checks independently. A `recommendation: DENY` from Sentinel does not block the action unless the gateway also denies it — but a DENY or CRITICAL finding triggers mandatory human review in workflows that require escalation.

### Sentinel failure handling

| Failure | Behavior |
|---|---|
| Sentinel times out | Proceed to gateway; finding recorded as `TIMEOUT`; gateway applies policy |
| Sentinel returns malformed output | Proceed to gateway; finding recorded as `MALFORMED`; gateway applies policy |
| Sentinel model call fails | Proceed to gateway; finding recorded as `UNAVAILABLE`; if policy requires Sentinel finding for this action type, gateway denies |
| Sentinel returns CRITICAL | Gateway receives the finding; gateway still applies its own checks independently |

Sentinel unavailability never converts a gateway denial into an approval.

---

## Policy Enforcement Gateway — deterministic authorization

The gateway is the final authority. It makes its decision in application code, not via an LLM. The sequence of checks:

### Check order

1. **Tool allowlist** — Is `tool_name` on the configured list of permitted tools? If not: deny immediately.
2. **Principal identity** — Does the requesting agent/session have permission to call this tool category?
3. **Argument type validation** — Do the arguments match the expected typed schema for this tool? Reject malformed or unexpected arguments.
4. **Destination allowlist** — For tools that take a destination (e.g., `send_report`): is the destination in the configured `ALLOWED_EMAIL_DESTINATIONS`? If not: deny.
5. **Resource permission** — Is the agent permitted to access the referenced resource (customer record, document)?
6. **Approval requirement** — Does this action type require a prior approval? If yes:
   a. Is an approval record present for this action?
   b. Is the approval bound to the exact `tool_name` and `arguments_hash`?
   c. Is the approval within its configured TTL (`APPROVAL_TTL_SECONDS`)?
   d. Has the approval already been used (single-use enforced)?
7. **Policy state resolvability** — Can all required policy records be read from PostgreSQL? If not: deny (fail-closed).

### Gateway output

```python
@dataclass
class GatewayDecision:
    authorized: bool
    reason: str
    failed_check: str | None        # which check caused denial
    approval_id: str | None         # if approval was consumed
    decided_at: datetime
```

### Fail-closed principle

If any check cannot be completed — database unavailable, policy record missing, approval state unresolvable — the gateway denies the action. There is no partial authorization.

```
If in doubt → deny.
```

---

## Approval binding

Approvals are bound to a specific action, not to a class of actions:

```python
@dataclass
class Approval:
    id: str
    action_hash: str                # SHA-256 of tool_name + sorted arguments
    bound_tool: str
    bound_arguments_hash: str
    approved_by: str
    approved_at: datetime
    expires_at: datetime            # now + APPROVAL_TTL_SECONDS
    used_at: datetime | None        # set on consumption; prevents reuse
```

An approval for `send_report(destination="reports@internal.example")` does not authorize `send_report(destination="reports@attacker.example")` — the `arguments_hash` will not match.

An approval that has expired (`expires_at < now`) is always denied, regardless of how recently it was granted.

An approval that has already been used (`used_at is not None`) is denied. Approvals are single-use.

---

## Simulated execution

If the gateway authorizes the action, the orchestration service calls the appropriate simulated tool via the sandbox infrastructure:

```
gateway.authorize(proposal) → GatewayDecision(authorized=True)
  └─► sandbox.execute(tool_name, arguments) → ToolResult
        ├── records what would have been sent/done
        ├── returns a realistic synthetic result
        └── never makes real network calls
```

The sandbox has no real transport. `send_report` records the call to the database and returns a success acknowledgement. No real email is sent.

### Tool failure handling

If a simulated tool raises an error: the result is recorded as an error outcome, an audit event is written, and the error is returned to the orchestration layer. The tool does not fabricate a success response.

---

## Audit trail

Every step in the action review and execution path produces an audit event:

| Step | Event type |
|---|---|
| Proposal received | `action_proposed` |
| Sentinel review started | `sentinel_review_started` |
| Sentinel finding produced | `sentinel_finding` |
| Gateway check started | `gateway_check_started` |
| Gateway decision | `gateway_authorized` or `gateway_denied` |
| Tool execution started | `tool_execution_started` |
| Tool execution result | `tool_execution_completed` or `tool_execution_failed` |
| Approval consumed | `approval_consumed` |

These events flow to the SSE publisher so the dashboard can display the real-time action timeline.

---

## Key invariants enforced here

From the security invariants in the README:

- **Invariant 4:** All proposed actions pass through the deterministic gateway. No bypass path.
- **Invariant 5:** Sentinel reviews actions before execution where the workflow requires review.
- **Invariant 6:** Sentinel findings cannot override an explicit policy denial.
- **Invariant 10:** Sensitive actions require policy-defined approval.
- **Invariant 11:** Approval applies only to the exact validated action and arguments, and expires per policy.
- **Invariant 12:** Tool execution remains within the sandbox.
- **Invariant 14:** If a required authorization check cannot be completed, the action is denied.
- **Invariant 15:** Failure of Sentinel must never disable deterministic authorization.

---

## Related documents

- Architecture overview → [`overview.md`](overview.md)
- Memory lifecycle → [`memory-lifecycle.md`](memory-lifecycle.md)
- Security invariants → [`../security/security-invariants.md`](../security/security-invariants.md)
- API contract for approvals and tool calls → [`../api/api-contract.md`](../api/api-contract.md)
