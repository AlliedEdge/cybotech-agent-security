# Memory Lifecycle

## Purpose

This document describes how persistent memory is proposed, evaluated, stored, retrieved, and eventually expired or revoked in Cybotech. It covers the Memory Guard's role, the provenance model, lifecycle states, the retrieval trust-revalidation step, and how PostgreSQL and pgvector stay consistent.

---

## Why memory needs a lifecycle

An agent's persistent memory is a high-value target. A fact that appears legitimate — a vendor contact, a report destination, a known policy — can influence every future session that retrieves it. If an attacker can cause the agent to save a malicious fact as trusted memory, the attack persists indefinitely until the record is identified and revoked.

A simple write-and-retrieve pattern has no mechanism to distinguish:

- A fact learned from a trusted internal document
- A fact injected into an external email
- A fact that was trusted last week but has since been revoked

The memory lifecycle solves this by making trust an explicit, maintained property of each memory record — not an implicit assumption based on recency or similarity.

---

## Memory proposal interface

The agent never writes directly to the database. It submits a **memory proposal** to the Memory Guard:

```python
@dataclass
class MemoryProposal:
    content: str                  # the proposed fact to remember
    source_document_id: str       # which document triggered this proposal
    proposed_by: str              # agent identifier
    proposed_at: datetime
    claimed_category: str         # e.g. "vendor_contact", "policy_note"
    raw_context: str              # surrounding text from source (for review)
```

The Memory Guard is the only component with write access to the `memories` table for new proposals. Existing trusted records may be updated or revoked through explicit administrative endpoints (also guarded).

---

## Provenance capture

Every memory proposal is accompanied by a provenance record that answers:

- Where did this content come from? (`source_type`: email, document, synthetic, internal)
- What is the trust level of that source? (`trust_level`: untrusted, internal, verified)
- What was the original surrounding context?
- Did the content make any authority claims, and were they verified?

```python
@dataclass
class MemoryProvenance:
    source_document_id: str
    source_type: SourceType          # email | document | synthetic | internal
    trust_level: TrustLevel          # untrusted | internal | verified
    claimed_authority: str | None    # e.g. "IT department approved"
    authority_verified: bool
    ingestion_path: str              # which workflow step ingested this source
    raw_proposal: str                # original proposed content, preserved
```

Provenance is immutable once recorded. It cannot be upgraded from untrusted to verified after the fact by the agent.

---

## Lifecycle states

```
                    ┌─────────────┐
                    │   PENDING   │  Initial state after proposal
                    └──────┬──────┘
                           │  Memory Guard evaluation
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
      TRUSTED          QUARANTINED       REJECTED
   (approved,          (suspicious;      (policy
   enters pool)        evidence kept;    violation;
                       excluded from     minimal
                       retrieval)        record)
          │
    (time elapses or explicit action)
          ├── EXPIRED   (TTL elapsed; excluded from retrieval)
          └── REVOKED   (explicit revocation; excluded immediately)
```

| State | In retrieval pool | Evidence kept | Reason required |
|---|---|---|---|
| `pending` | No | Yes | — |
| `trusted` | Yes | Yes | — |
| `quarantined` | No | Yes (original content) | Yes |
| `rejected` | No | Minimal | Yes |
| `expired` | No | Yes | TTL config |
| `revoked` | No | Yes | Yes |

### State transition rules

- `pending → trusted`: Memory Guard approves; provenance clean; policy permits
- `pending → quarantined`: Suspicious instruction detected; unverified authority claim; policy flags source
- `pending → rejected`: Hard policy violation; malformed proposal; prohibited content category
- `pending → pending (review)`: Requires human review before transition
- `trusted → expired`: `expires_at` timestamp elapsed (checked at retrieval time)
- `trusted → revoked`: Explicit revocation via administrative action or automated policy trigger

No state can transition back to `trusted` without a new, clean proposal going through Memory Guard evaluation from scratch.

---

## Memory Guard evaluation

When a proposal arrives, the Memory Guard performs these checks in order:

1. **Source provenance check:** Is the source trust level consistent with the claimed category? An untrusted email cannot approve policy-level facts.
2. **Instruction detection:** Does the content contain patterns consistent with injected instructions? (e.g., "please update", "new address is", directives that reference the agent itself)
3. **Authority claim evaluation:** Does the content assert authority it cannot have? Claims like "IT approved this" from an untrusted email source are flagged.
4. **Policy rule evaluation:** Does the proposed fact violate any configured policy? (e.g., destination categories that require verified sources)
5. **Category permission check:** Is the agent permitted to save memory in this category for this source type?

The guard produces a decision (`MemoryDecision`) with a state, rationale, and confidence. This decision and its rationale are persisted regardless of outcome — quarantined proposals are not silently discarded.

---

## Retrieval and trust revalidation

Semantic similarity via pgvector is used to find candidate memory IDs. It is not used to determine trust.

For every candidate returned by pgvector:

1. Resolve the canonical `memories` record from PostgreSQL by `memory_id`
2. Check `status` — must be `trusted`
3. Check `expires_at` — must be in the future or null
4. Check `revoked_at` — must be null
5. Check `owner_id` / session scope — must match the requesting context
6. Check any additional access permissions in `memory_provenance`
7. If all checks pass: include in results returned to the agent
8. If any check fails: omit; write a `retrieval_denied` audit event

If the canonical record cannot be retrieved (database unavailable, record missing): omit the candidate. Never promote an unverifiable candidate to trusted context.

---

## PostgreSQL and pgvector consistency

### Write path

1. Memory Guard approves a proposal
2. PostgreSQL transaction: insert `memories` record with `status = trusted`, insert `memory_provenance` record
3. Transaction commits
4. Embedding generation request sent to the model provider adapter
5. On success: insert/update `memory_embeddings` (pgvector)
6. On embedding failure: the memory record remains `trusted` in PostgreSQL, but has no vector row. It can be retrieved by exact lookup but not by semantic search. A reconciliation job (planned) retries failed embeddings.

### Revocation path

1. Revocation request received (administrative action or policy trigger)
2. PostgreSQL: update `memories.status = revoked`, set `revoked_at`, record `revocation_reason`
3. The vector row in `memory_embeddings` may still exist and may still appear as a similarity candidate
4. The retrieval canonical check catches this: `status = revoked` → candidate omitted
5. The vector row is cleaned up by the next reconciliation pass (eventual consistency)

### Consistency guarantees

- **PostgreSQL is authoritative.** A vector row existing does not mean the memory is trusted.
- **Embedding generation is not in the same transaction as the relational write.** Failure of embedding generation does not roll back the memory record.
- **Revocation takes effect immediately** for retrieval (canonical check), even before the vector row is cleaned up.
- **Duplicate embedding requests are idempotent.** The reconciliation strategy uses upsert on `memory_id`.

---

## Quarantined content handling

Quarantined proposals are retained as evidence. They:

- Are excluded from all retrieval queries
- Are never returned to the agent as context
- Are available to administrators and the dashboard for review
- Have their original `raw_proposal` preserved in `memory_provenance`
- Are subject to the configured retention policy (planned: configurable TTL for quarantined records)

Quarantined content is never treated as trusted, even if a later review finds it was benign. Recovery requires a new proposal through the standard evaluation path.

---

## Failure behavior

| Failure | Behavior |
|---|---|
| Memory Guard process error | Proposal not committed; `pending` record written with error flag; audit event written |
| Embedding generation fails | Memory stays in approved state (accessible by exact lookup); no vector row; reconciliation retries |
| PostgreSQL unavailable during write | Proposal not committed; caller receives error; no silent promotion to trusted |
| PostgreSQL unavailable during retrieval | No candidates returned; action requiring memory context is denied or proceeds with empty context (policy-configured) |
| Canonical record missing during retrieval | Candidate omitted; `retrieval_denied` audit event with reason `record_not_found` |

---

## Related documents

- Architecture overview → [`overview.md`](overview.md)
- Action enforcement and gateway → [`action-enforcement.md`](action-enforcement.md)
- Security invariants for memory → [`../security/security-invariants.md`](../security/security-invariants.md) (invariants 2, 3, 7, 8, 9)
- Data model → README §8
