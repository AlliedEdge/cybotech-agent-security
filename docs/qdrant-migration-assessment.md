# Qdrant Vector Database Migration Assessment

**Branch:** `feat/qdrant-vector-db-migration`
**Date:** 2026-11-01
**Author:** Senior Backend Architect / Vector Database Specialist

---

## 1. Executive Summary

Cybotech currently uses PostgreSQL + pgvector for all vector storage and similarity search. This assessment documents the current architecture, identifies every vector dependency, and defines a safe migration to a dedicated Qdrant vector database while preserving PostgreSQL as the canonical system of record and keeping the multi-agent security architecture intact.

---

## 2. Current Architecture

### 2.1 Technology Stack

| Layer | Technology |
|---|---|
| Application framework | FastAPI + LangGraph |
| Primary database | PostgreSQL 16 (pgvector/pgvector:pg16 image) |
| Vector extension | pgvector 0.3.5+ |
| ORM | SQLAlchemy 2.0 |
| Migrations | Alembic |
| Agent orchestration | LangGraph StateGraph |
| Embedding (production) | OpenAI `text-embedding-3-small`, 384 dimensions |
| Embedding (offline/test) | `LocalEmbedder` — deterministic hash-based, 384 dimensions, model name `local-token-hash-384-v1` |

### 2.2 Vector Columns in PostgreSQL

#### `memories` table (`backend/src/cybotech/infrastructure/database/models.py`)

```python
embedding: Mapped[Any] = mapped_column(
    Vector(384).with_variant(JSON, "sqlite"), nullable=True
)
embedding_model: Mapped[Any] = mapped_column(String(200), nullable=True)
```

- Dimension: **384**
- Nullable: yes (embedding is generated lazily at proposal time if embedder is wired, or during `transition → trusted`)
- No ANN index (HNSW / IVFFlat) — every memory query is a full sequential scan
- No `session_id` column — isolation is by trust state and provenance, not by session

#### `document_chunks` table

```python
embedding: Mapped[Any] = mapped_column(
    Vector(384).with_variant(JSON, "sqlite"), nullable=False
)
embedding_model: Mapped[str] = mapped_column(String(200), nullable=False)
```

- Dimension: **384**
- Nullable: no — every chunk must have an embedding at ingest time
- No ANN index — every document retrieval query is a full sequential scan
- Session isolation is enforced via `DocumentRecord.session_id` in the JOIN

### 2.3 Alembic Migrations

| File | Description |
|---|---|
| `20261030_0001_security_foundation.py` | `CREATE EXTENSION IF NOT EXISTS vector`; creates `memories(embedding Vector(384))`, audit_events, policy_decisions, memory_provenance |
| `20261031_0002_documents.py` | Creates `documents`, `document_chunks(embedding Vector(384))` |

Both migrations import `from pgvector.sqlalchemy import Vector`.

### 2.4 Vector Similarity Search Implementations

#### Memory retrieval (`services/retrieval/service.py`)

**Dual-path — PostgreSQL uses pgvector SQL, SQLite falls back to Python:**

```python
# PostgreSQL path:
statement.order_by(Memory.embedding.cosine_distance(vector)).limit(...)

# SQLite / test path:
candidates.sort(key=lambda memory: _cosine_distance(memory.embedding, vector))
```

Pre-filter in SQL: `status == trusted`, `revoked_at IS NULL`, `expires_at > now`, `embedding_model == model_name`.  
Post-filter in Python: `is_eligible(m)` provenance check + `min_similarity` threshold.

#### Document retrieval (`services/documents/service.py`)

**Always Python-side — no pgvector SQL path even on PostgreSQL:**

```python
rows = self.db.execute(SELECT DocumentChunk JOIN DocumentRecord WHERE session_id=... )
ranked = sorted(rows, key=lambda row: _distance(row[0].embedding, vector))[:top_k]
```

Loads **all** session-matching chunks into Python memory for cosine ranking — an O(N) full dataset scan.

### 2.5 Embedding Providers

| Provider | Class | model_name | Dimensions | Notes |
|---|---|---|---|---|
| OpenAI production | `OpenAIEmbedder` | `text-embedding-3-small` | 384 | Network call; used in live assistant |
| Local offline | `LocalEmbedder` | `local-token-hash-384-v1` | 384 | Hash-based bag-of-words; NOT semantic; used in red-team lab and unit tests |

Both are injected via the `Embedder` Protocol — the same instance is wired into `MemoryGuard`, `RetrievalService`, and `DocumentService` via `build_security_ports`.

### 2.6 Session Isolation

- **Documents:** `DocumentRecord.session_id` column; SQL WHERE clause enforces session scope at retrieval.
- **Memory:** No session_id; isolation is by trust state (`status == trusted`), revocation, expiry, and provenance (`trust_level in {verified, internal}` or `review_verified == True`).

### 2.7 Memory Trust Lifecycle

```
proposed → pending
pending  → trusted | quarantined | rejected
trusted  → expired | revoked
quarantined → trusted | rejected | revoked
```

- `MemoryGuard.propose_memory()`: regex checks, provenance resolution, optional reviewer
- `MemoryGuard.transition()`: state machine enforced; embedding generated lazily on `→ trusted`
- `is_eligible()`: canonical trust gate used by both retrieval and tests — this is the security invariant
- **PostgreSQL is the sole authority on trust**; Qdrant metadata is never trusted for authorization

### 2.8 Multi-Agent Architecture

| Agent | Role | Uses vector search? |
|---|---|---|
| `SupervisorGraph` | Coordinates all agents, owns handoff budget | Indirectly via PrimaryAssistant |
| `PrimaryAssistant` | Reasoning + retrieval proposals | Yes — document + memory retrieval |
| `MemoryGuardAgent` | Advisory memory review | No (trust enforced by lifecycle service) |
| `SentinelAgent` | Advisory action review | No |
| `VibeHackerGraph` | Red-team isolated experiments | Yes — via LocalEmbedder |
| `PolicyGateway` | Deterministic authorization | No — uses PostgreSQL only |

### 2.9 Port / Adapter Wiring

```
build_security_ports(db, embedder, ...)
  → MemoryGuardPort      → MemoryGuard(db, embedder)
  → TrustedRetrievalPort → RetrievalService(db, embedder)   ← pgvector SQL path
  → DocumentRetrievalAdapter → DocumentService(db, embedder) ← Python cosine path
  → SentinelPortAdapter  → Sentinel()
  → PolicyGatewayPort    → PolicyGateway(db)
```

### 2.10 Docker Compose (Before Migration)

```
db        pgvector/pgvector:pg16   port 127.0.0.1:5432
backend   FastAPI                  port 127.0.0.1:8000
frontend  React/Vite               port 127.0.0.1:5173
```

---

## 3. Target Architecture

```
PostgreSQL 16     — canonical records, trust lifecycle, metadata, audit
Qdrant 1.9        — vector storage, ANN search, metadata filtering
FastAPI           — API layer
LangGraph         — multi-agent orchestration
```

### PostgreSQL retains

- All relational records (memories, document_records, document_chunks, audit_events, policy_decisions)
- Trust lifecycle state machine
- Provenance and revocation
- Vector synchronization outbox (new table)
- `embedding` and `embedding_model` columns retained (not null-ed out) until full migration verified

### Qdrant owns

- `cybotech_document_chunks` collection — 384-dim cosine vectors + payload metadata
- `cybotech_memory_vectors` collection — 384-dim cosine vectors + payload metadata
- ANN search (HNSW, cosine distance)
- Payload indexes for filtered search

---

## 4. Affected Components

### Files modified

| File | Change |
|---|---|
| `compose.yaml` | Add `qdrant` service |
| `.env.example` | Add Qdrant env vars |
| `backend/.env.example` | Add Qdrant env vars |
| `backend/pyproject.toml` | Add `qdrant-client` dependency |
| `backend/src/cybotech/infrastructure/database/models.py` | Add `VectorSyncOutbox` model |
| `backend/src/cybotech/services/documents/service.py` | Route vector ops to Qdrant |
| `backend/src/cybotech/services/retrieval/service.py` | Route vector ops to Qdrant |
| `backend/src/cybotech/services/memory_guard/service.py` | Route vector ops to Qdrant |
| `backend/src/cybotech/services/integration/ports.py` | Wire `VectorStore` into port builders |
| `backend/migrations/versions/` | Add outbox migration |

### Files created

| File | Description |
|---|---|
| `backend/src/cybotech/infrastructure/vector_store/__init__.py` | Package |
| `backend/src/cybotech/infrastructure/vector_store/interface.py` | Abstract port + typed contracts |
| `backend/src/cybotech/infrastructure/vector_store/qdrant_adapter.py` | Qdrant implementation |
| `backend/src/cybotech/infrastructure/vector_store/collections.py` | Collection configs |
| `backend/src/cybotech/infrastructure/vector_store/outbox.py` | Outbox dispatcher |
| `backend/src/cybotech/cli/migrate_vectors.py` | pgvector → Qdrant migration command |
| `backend/src/cybotech/cli/reconcile_vectors.py` | Reconciliation admin command |
| `backend/tests/unit/test_qdrant_adapter.py` | Qdrant adapter unit tests |
| `backend/tests/integration/test_qdrant_integration.py` | Real Qdrant integration tests |
| `backend/tests/integration/test_qdrant_document_retrieval.py` | Document pipeline integration |
| `backend/tests/integration/test_qdrant_memory_retrieval.py` | Memory pipeline integration |
| `docs/qdrant-migration-assessment.md` | This document |
| `docs/qdrant-operations.md` | Admin, backup, restore, troubleshooting |

---

## 5. Migration Plan (Phase Summary)

| Phase | Description |
|---|---|
| 3 | Add Qdrant Docker service (pinned image, volume, API key auth, health check) |
| 4 | Add env vars (`QDRANT_URL`, `QDRANT_API_KEY`, collection names) |
| 5 | Create `VectorStore` interface and `QdrantVectorStore` adapter |
| 6 | Migrate `DocumentService`: write embeddings to Qdrant, search via Qdrant |
| 7 | Migrate `RetrievalService` and `MemoryGuard`: write/search via Qdrant |
| 8 | Add `VectorSyncOutbox` table + dispatcher for consistency |
| 9 | Validate embedding dimensions on every write/search; reject mismatches |
| 10 | `migrate-vectors` CLI command: export pgvector → Qdrant, verify, report |
| 11 | Update `build_security_ports` and `build_persistent_graph` to inject `VectorStore` |
| 12 | Unit tests (mock Qdrant), integration tests (real Qdrant via `qdrant-client`) |
| 13 | Update all documentation |

---

## 6. Migration Risks

| Risk | Severity | Mitigation |
|---|---|---|
| PostgreSQL and Qdrant cannot share a transaction | High | Transactional outbox pattern; Qdrant write after PG commit |
| Qdrant unavailable during write | High | Outbox retries; writes recorded as `pending`; never silently marked `indexed` |
| Embedding dimension mismatch (model change) | High | Validate `len(vector) == 384` on every upsert; reject if wrong model name |
| Qdrant metadata trusted for authorization | Critical | Policy Gateway and `is_eligible()` always re-read from PostgreSQL |
| Python-side cosine ranking at scale | Medium | Replaced by Qdrant ANN — no longer a bottleneck |
| Missing vectors after restart | Medium | Reconciliation command detects and repairs |
| Cross-session data leak via Qdrant | High | All Qdrant results are IDs only; canonical records re-fetched from PostgreSQL with session_id enforcement |
| Test suite breakage | Medium | Tests updated; SQLite fallback preserved for unit tests |

---

## 7. Embedding Compatibility Notes

- **Production model:** `text-embedding-3-small` via OpenAI API, 384 dimensions
- **Test/offline model:** `local-token-hash-384-v1` (hash-based, NOT semantic — never label as semantic)
- **Both models produce 384-dim L2-normalized vectors** → cosine distance is valid
- **No model change during this migration** — existing `embedding_model` column values preserved
- Qdrant collections configured with `distance=Cosine`, `size=384`
- Dimension mismatch raises `ValueError` before any Qdrant write

---

## 8. Data Migration Strategy

1. Inspect existing `document_chunks` and `memories` for row counts and `embedding_model` distribution
2. Create Qdrant collections (idempotent — `recreate_collection` only if not exists)
3. Export vectors from PostgreSQL in batches of 256
4. Upsert to Qdrant using stable deterministic UUIDs (derived from chunk/memory UUID)
5. Verify Qdrant counts match PostgreSQL counts per model
6. Run representative search — verify top result IDs match pgvector results
7. Mark migration complete in report
8. pgvector columns are NOT dropped until the operator explicitly runs the removal migration

---

## 9. Post-Migration Cleanup (Separate Step, Requires Explicit Authorization)

- Remove pgvector SQL ordering from `RetrievalService`
- Remove `embedding` and `embedding_model` columns from ORM models
- Remove `pgvector` from `pyproject.toml`
- Remove `CREATE EXTENSION vector` from future fresh-install migrations
- Add Alembic migration to drop vector columns

**This step must NOT be run automatically.** It requires:
1. A verified Qdrant migration report
2. Explicit operator confirmation
3. A database backup before destructive changes

---

## 10. Security Invariants Preserved

1. **PostgreSQL is the authority on memory trust** — `is_eligible()` always re-runs against live DB rows
2. **Qdrant metadata cannot grant trust** — Policy Gateway never reads Qdrant payload for authorization
3. **Session isolation enforced in PostgreSQL** — Qdrant results are chunk IDs; PostgreSQL re-fetches with `session_id` filter
4. **Evidence remains untrusted** — `DocumentEvidence.trust_label == "untrusted"` unchanged
5. **Memory Guard lifecycle unchanged** — propose → pending → trust transition guarded by `MemoryGuard.transition()`
6. **Sentinel and Gateway are independent** — neither receives Qdrant metadata
