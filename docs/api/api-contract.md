# API Contract

> **Status: Proposed design.** None of these endpoints are implemented yet. This document defines the intended contract for implementation starting in Week 1.

## Overview

All API routes are versioned under `/api/v1`. The API is served by FastAPI with Pydantic v2 validation on all inputs and outputs. The live event stream is a Server-Sent Events (SSE) endpoint at `/api/v1/events`.

Base URL (local development): `http://localhost:8000`

---

## General conventions

### Request format

- `Content-Type: application/json` for all POST/PATCH bodies
- Query parameters use `snake_case`
- Path parameters use the resource's `id` field (ULID strings)

### Response format

All responses wrap the payload in a consistent envelope:

```json
{
  "data": { ... },
  "meta": {
    "request_id": "req_01J8K2X9P3RQMN4T5V6W7Y8Z",
    "timestamp": "2026-10-29T14:32:01.123Z"
  }
}
```

List responses include pagination metadata:

```json
{
  "data": [ ... ],
  "meta": {
    "request_id": "...",
    "timestamp": "...",
    "pagination": {
      "page": 1,
      "page_size": 50,
      "total": 142,
      "has_next": true
    }
  }
}
```

### Error format

```json
{
  "error": {
    "code": "DESTINATION_NOT_ALLOWED",
    "message": "The requested destination is not on the allowlist.",
    "detail": "destination='reports@attacker.example' is not in ALLOWED_EMAIL_DESTINATIONS",
    "request_id": "req_01J8K2X9P3RQMN4T5V6W7Y8Z"
  }
}
```

### HTTP status codes

| Code | Meaning |
|---|---|
| `200` | Success |
| `201` | Created |
| `202` | Accepted (async operation started) |
| `400` | Validation error (malformed input) |
| `401` | Unauthenticated (planned: session auth) |
| `403` | Forbidden (policy denial) |
| `404` | Resource not found |
| `409` | Conflict (duplicate, state mismatch) |
| `422` | Unprocessable entity (Pydantic validation failure) |
| `500` | Internal server error |
| `503` | Service unavailable (dependency down) |

---

## Route table

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/health` | Service health and dependency status |
| `POST` | `/api/v1/sessions` | Create a new assistant session |
| `GET` | `/api/v1/sessions/{id}` | Fetch session details |
| `POST` | `/api/v1/sessions/{id}/tasks` | Submit a task to the assistant |
| `POST` | `/api/v1/documents` | Ingest a document or email |
| `GET` | `/api/v1/documents/{id}` | Fetch document metadata |
| `GET` | `/api/v1/memories` | List memory records with filters |
| `GET` | `/api/v1/memories/{id}` | Fetch a single memory record |
| `POST` | `/api/v1/memories/{id}/revoke` | Revoke a trusted memory entry |
| `GET` | `/api/v1/security-events` | List security events |
| `GET` | `/api/v1/security-events/{id}` | Fetch a single security event |
| `GET` | `/api/v1/attack-cases` | List available attack scenarios |
| `POST` | `/api/v1/attack-runs` | Start a red-team run |
| `GET` | `/api/v1/attack-runs/{id}` | Fetch run status and outcomes |
| `POST` | `/api/v1/evaluations` | Start an evaluation |
| `GET` | `/api/v1/evaluations/{id}` | Fetch evaluation results |
| `GET` | `/api/v1/tool-calls` | List tool call records |
| `GET` | `/api/v1/tool-calls/{id}` | Fetch a single tool call |
| `GET` | `/api/v1/policies` | List active policy rules |
| `GET` | `/api/v1/approvals/{action_hash}` | Check approval state |
| `POST` | `/api/v1/approvals` | Grant an approval |
| `GET` | `/api/v1/events` | SSE live event stream |

---

## Endpoint specifications

### `GET /api/v1/health`

Returns the health of the service and its dependencies.

**Response `200`:**
```json
{
  "data": {
    "status": "healthy",
    "version": "0.1.0",
    "dependencies": {
      "database": "healthy",
      "pgvector": "healthy",
      "llm_provider": "healthy"
    }
  }
}
```

**Response `503`** when any dependency is unhealthy:
```json
{
  "data": {
    "status": "degraded",
    "dependencies": {
      "database": "unhealthy",
      "pgvector": "unknown",
      "llm_provider": "healthy"
    }
  }
}
```

---

### `POST /api/v1/sessions`

Creates a new assistant session.

**Request body:**
```json
{
  "user_id": "user_01J8K2X0ABCDEF123456789",
  "configuration": "full"
}
```

`configuration` enum: `baseline | memory_guard | policy_gateway | full`

**Response `201`:**
```json
{
  "data": {
    "id": "sess_01J8K2X0ABCDEF123456789",
    "user_id": "user_01J8K2X0ABCDEF123456789",
    "configuration": "full",
    "status": "active",
    "created_at": "2026-10-29T14:32:01.123Z"
  }
}
```

---

### `POST /api/v1/sessions/{id}/tasks`

Submits a task to the assistant in an active session. This is the primary agent entry point.

**Request body:**
```json
{
  "task_description": "Summarise the open support tickets from Acme Corp and send the weekly report.",
  "context_document_ids": ["doc_01J8K2X0GHIJKL987654321"]
}
```

**Response `202`** (task accepted, processing async):
```json
{
  "data": {
    "task_id": "task_01J8K2X9P3RQMN4T5V6W7Y8Z",
    "session_id": "sess_01J8K2X0ABCDEF123456789",
    "status": "processing",
    "submitted_at": "2026-10-29T14:32:01.123Z"
  }
}
```

Task progress and completion are delivered via the SSE stream (`event: task_completed`).

---

### `POST /api/v1/documents`

Ingests a document or email into a session. The content is stored with provenance metadata; it is not immediately trusted.

**Request body:**
```json
{
  "session_id": "sess_01J8K2X0ABCDEF123456789",
  "source_type": "email",
  "trust_level": "untrusted",
  "content": "Subject: Vendor update\n\nPlease update the reporting address...",
  "metadata": {
    "sender": "vendor@example.com",
    "subject": "Vendor update",
    "received_at": "2026-10-29T13:00:00Z"
  }
}
```

`source_type` enum: `email | document | synthetic | internal`
`trust_level` enum: `untrusted | internal | verified`

**Validation:** `trust_level = verified` requires an additional verified-source token (planned). Default for email is always `untrusted`.

**Response `201`:**
```json
{
  "data": {
    "id": "doc_01J8K2X0GHIJKL987654321",
    "session_id": "sess_01J8K2X0ABCDEF123456789",
    "source_type": "email",
    "trust_level": "untrusted",
    "ingested_at": "2026-10-29T14:32:01.123Z"
  }
}
```

The raw content is stored but not echoed in the response to avoid unintentional logging.

---

### `GET /api/v1/memories`

Lists memory records. Supports filtering by status, session, and date range.

**Query parameters:**

| Parameter | Type | Description |
|---|---|---|
| `status` | string | Filter by lifecycle state |
| `session_id` | string | Filter to a specific session |
| `source_type` | string | Filter by source type |
| `page` | int | Page number (default: 1) |
| `page_size` | int | Results per page (default: 50, max: 200) |

**Response `200`:** list of memory records with status, provenance summary, and timestamps. Raw content is not returned in list responses.

---

### `POST /api/v1/memories/{id}/revoke`

Revokes a trusted memory entry immediately. Requires an explicit reason.

**Request body:**
```json
{
  "reason": "Memory originated from malicious email content identified in attack run run_01J8K2X0MNOPQR456789012"
}
```

**Response `200`:**
```json
{
  "data": {
    "id": "mem_01J8K2X0MNOPQR456789012",
    "status": "revoked",
    "revoked_at": "2026-10-29T14:35:00Z",
    "revocation_reason": "Memory originated from malicious email..."
  }
}
```

**Authorization:** This endpoint requires elevated permission (planned). It is not callable by the agent itself — only by an administrative action or the evaluation harness.

---

### `POST /api/v1/attack-runs`

Starts a red-team experiment run using a named attack case.

**Request body:**
```json
{
  "attack_case_id": "case_01J8K2X0PQRSTU123456789",
  "configuration": "baseline",
  "seed": 42
}
```

`configuration` enum: `baseline | memory_guard | policy_gateway | full`

**Response `202`:**
```json
{
  "data": {
    "run_id": "run_01J8K2X0GHIJKL987654321",
    "attack_case_id": "case_01J8K2X0PQRSTU123456789",
    "configuration": "baseline",
    "status": "running",
    "started_at": "2026-10-29T14:32:01.123Z"
  }
}
```

**Constraints:** Only one run may execute at a time (planned: queue). The harness enforces `RED_TEAM_MAX_ITERATIONS`.

---

### `POST /api/v1/approvals`

Grants an approval for a pending action. Approvals are bound to an exact action hash.

**Request body:**
```json
{
  "action_hash": "sha256:a3f1b2c4d5e6f7...",
  "bound_tool": "send_report",
  "bound_arguments_hash": "sha256:b4c2d3e4f5a6b7...",
  "approved_by": "user_01J8K2X0ABCDEF123456789"
}
```

**Response `201`:**
```json
{
  "data": {
    "id": "appr_01J8K2X0VWXYZ123456789",
    "action_hash": "sha256:a3f1b2c4d5e6f7...",
    "bound_tool": "send_report",
    "approved_at": "2026-10-29T14:32:01.123Z",
    "expires_at": "2026-10-29T14:37:01.123Z"
  }
}
```

`expires_at` is `approved_at + APPROVAL_TTL_SECONDS`. The approval is single-use.

---

## SSE event stream

### `GET /api/v1/events`

Returns a Server-Sent Events stream of live security events.

**Headers:**
```
Accept: text/event-stream
Cache-Control: no-cache
Last-Event-ID: evt_01J8K2X9P3RQMN4T5V6W7Y8Z  (optional; for reconnect replay)
```

**Response:** `200 text/event-stream`

```
id: evt_01J8K2X9P3RQMN4T5V6W7Y8Z
event: memory_quarantined
data: {"id":"evt_01J8K2X9P3RQMN4T5V6W7Y8Z","event":"memory_quarantined","timestamp":"2026-10-29T14:32:01.123Z","session_id":"sess_01J8K2X0ABCDEF123456789","run_id":null,"payload":{"memory_id":"mem_01J8K2X0MNOPQR456789012","reason":"instruction_in_untrusted_content","source_type":"email","trust_level":"untrusted"}}

id: evt_01J8K2X9P3RQMN4T5V6W7Y8ZZ
event: gateway_denied
data: {"id":"evt_01J8K2X9P3RQMN4T5V6W7Y8ZZ","event":"gateway_denied","timestamp":"2026-10-29T14:32:05.456Z","session_id":"sess_01J8K2X0ABCDEF123456789","run_id":null,"payload":{"tool_name":"send_report","failed_check":"destination_not_allowed","destination":"reports@attacker.example"}}

```

### SSE event envelope

```typescript
interface SseEvent {
  id: string;              // ULID — unique event identifier
  event: SseEventType;     // event type string (see below)
  timestamp: string;       // ISO 8601
  session_id: string | null;
  run_id: string | null;
  payload: Record<string, unknown>;  // event-specific data
}
```

### SSE event types

| Event type | Trigger |
|---|---|
| `document_ingested` | A document or email is ingested |
| `memory_proposed` | A memory proposal is submitted to the Memory Guard |
| `memory_approved` | Memory Guard approves a proposal → status trusted |
| `memory_quarantined` | Memory Guard quarantines a proposal |
| `memory_rejected` | Memory Guard rejects a proposal |
| `memory_revoked` | A trusted memory is revoked |
| `retrieval_permitted` | A memory record passes retrieval checks |
| `retrieval_denied` | A memory candidate fails retrieval checks |
| `sentinel_finding` | Sentinel produces a pre-execution finding |
| `gateway_authorized` | Policy Gateway authorizes a tool call |
| `gateway_denied` | Policy Gateway denies a tool call |
| `tool_execution_completed` | Simulated tool call completes successfully |
| `tool_execution_failed` | Simulated tool call returns an error |
| `approval_consumed` | An approval is used for an authorized action |
| `attack_run_started` | A red-team run begins |
| `attack_run_completed` | A red-team run finishes |
| `task_completed` | An assistant task finishes |

### Reconnection and replay

1. The client sets `Last-Event-ID` to the ID of the last received event on reconnect
2. The server replays all events from `audit_events` with `sequence_number > last_sequence` for the last-event-id
3. The client deduplicates events by `id` before rendering
4. If `Last-Event-ID` is absent, the server sends a configurable number of recent events (default: last 100) then switches to live streaming

**SSE is for observability only.** A missed or duplicate SSE event has no effect on any security decision.

---

## Validation rules

| Field | Rule |
|---|---|
| `session_id` | Must reference an existing active session |
| `document.trust_level` | Email sources always ingested as `untrusted`; cannot be overridden to `verified` without a verification token |
| `attack_run.configuration` | Must be one of the four defined configurations |
| `approval.expires_at` | Server-computed; not client-supplied |
| `memory revoke.reason` | Required; minimum 10 characters |
| Pagination `page_size` | Maximum 200; default 50 |

---

## Authorization behavior (planned)

Authentication and session authorization are planned for a future implementation milestone. The current scaffolding does not enforce user authentication. When implemented:

- Session creation will require a valid user identity
- Memory revocation will require elevated privilege
- Attack run initiation will require the experiment operator role
- All other read endpoints will require a valid session token

Until authentication is implemented, all endpoints are accessible locally. Do not expose the development server on a public network interface.

---

## Related documents

- Architecture overview → [`../architecture/overview.md`](../architecture/overview.md)
- Action enforcement → [`../architecture/action-enforcement.md`](../architecture/action-enforcement.md)
- Memory lifecycle → [`../architecture/memory-lifecycle.md`](../architecture/memory-lifecycle.md)
- Data model → README §8
