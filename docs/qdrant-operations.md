# Qdrant Operations Guide

**Branch:** `feat/qdrant-vector-db-migration`

This document covers deployment, administration, backup, restore, troubleshooting and
reconciliation for the Qdrant vector database used by Cybotech.

---

## 1. Service Overview

| Item | Value |
|---|---|
| Image | `qdrant/qdrant:v1.9.7` |
| Docker service | `qdrant` |
| Internal URL | `http://qdrant:6333` |
| Protocol | HTTP only (gRPC disabled) |
| Authentication | API key (`QDRANT_API_KEY`) |
| Storage volume | `qdrant_data` |
| Public exposure | **None** — loopback-bound only |

Qdrant is accessible only from within the Docker Compose network.
The backend connects using `QDRANT_URL=http://qdrant:6333`.
No external port binding and no Cloudflare Tunnel ingress.

---

## 2. Which Services Use Which Database

| Concern | PostgreSQL | Qdrant |
|---|---|---|
| User / session metadata | ✓ | — |
| Document records (canonical) | ✓ | — |
| Document chunk content | ✓ | — |
| Memory records and trust state | ✓ | — |
| Provenance and revocation | ✓ | — |
| Audit events | ✓ | — |
| Agent handoffs and traces | ✓ | — |
| Policy decisions and approvals | ✓ | — |
| Experiments and evaluations | ✓ | — |
| Vector sync outbox | ✓ | — |
| Document chunk vectors | — | ✓ |
| Memory vectors | — | ✓ |
| ANN similarity search | — | ✓ |
| Metadata-filtered search | — | ✓ |

**PostgreSQL is the system of record and the sole authority on trust.**
Qdrant stores only vectors and minimal non-sensitive payload metadata.
Search results from Qdrant are always revalidated in PostgreSQL before use.

---

## 3. Qdrant Collections

### `cybotech_document_chunks`

| Field | Value |
|---|---|
| Dimension | 384 |
| Distance | Cosine |
| HNSW m | 16 |
| HNSW ef_construct | 100 |

Payload per point (no document text stored):

```json
{
  "chunk_id": "<UUID>",
  "document_id": "<UUID>",
  "session_id": "<string>",
  "content_hash": "<sha256-hex>",
  "embedding_model": "text-embedding-3-small",
  "embedding_version": "1"
}
```

Payload indexes: `session_id`, `embedding_model`, `document_id`

### `cybotech_memory_vectors`

| Field | Value |
|---|---|
| Dimension | 384 |
| Distance | Cosine |
| HNSW m | 16 |
| HNSW ef_construct | 100 |

Payload per point (no memory content stored):

```json
{
  "memory_id": "<UUID>",
  "session_id": "",
  "provenance_reference": "<source_document_id>",
  "content_hash": "<sha256-hex>",
  "embedding_model": "text-embedding-3-small",
  "embedding_version": "1"
}
```

Payload indexes: `embedding_model`, `memory_id`

### Point ID derivation

All Qdrant point IDs are deterministic UUID v5 values derived from the canonical
PostgreSQL record UUID. This makes all upserts idempotent.

```
document chunk point_id = UUID5(NAMESPACE_DOCUMENT_CHUNK, chunk_id)
memory point_id         = UUID5(NAMESPACE_MEMORY, memory_id)
```

Namespace constants are fixed in `qdrant_adapter.py` and must never be changed
after the first deployment.

---

## 4. Startup

Collections are created automatically on first backend startup via
`ensure_collections()`, called from `runtime_vector_store()` in `security.py`.
The operation is idempotent — it will not recreate or wipe existing collections.

```bash
docker compose up --build
```

---

## 5. Secure Local Administration

**Never expose the Qdrant port publicly.** All admin operations run inside the
container or through Docker Compose exec.

```bash
# Health check
docker compose exec qdrant curl -s http://localhost:6333/healthz

# List collections
docker compose exec qdrant sh -c \
  'curl -sf -H "api-key: $QDRANT__SERVICE__API_KEY" http://localhost:6333/collections'

# Collection info (document chunks)
docker compose exec qdrant sh -c \
  'curl -sf -H "api-key: $QDRANT__SERVICE__API_KEY" \
    http://localhost:6333/collections/cybotech_document_chunks'
```

---

## 6. Backup

Qdrant supports snapshot-based backup. Snapshots are point-in-time consistent.

### Create a snapshot

```bash
# Snapshot the document collection
docker compose exec qdrant sh -c \
  'curl -sf -XPOST -H "api-key: $QDRANT__SERVICE__API_KEY" \
    http://localhost:6333/collections/cybotech_document_chunks/snapshots'

# Snapshot the memory collection
docker compose exec qdrant sh -c \
  'curl -sf -XPOST -H "api-key: $QDRANT__SERVICE__API_KEY" \
    http://localhost:6333/collections/cybotech_memory_vectors/snapshots'
```

### Copy snapshots to host

```bash
mkdir -p ./backups/qdrant
docker compose cp qdrant:/qdrant/storage/collections/cybotech_document_chunks/snapshots \
  ./backups/qdrant/
docker compose cp qdrant:/qdrant/storage/collections/cybotech_memory_vectors/snapshots \
  ./backups/qdrant/
```

### Full storage backup (alternative)

```bash
docker compose stop qdrant
docker run --rm \
  -v cybotech-agent-security_qdrant_data:/data \
  -v $(pwd)/backups:/backup \
  alpine tar czf /backup/qdrant-storage-$(date +%Y%m%d).tar.gz -C /data .
docker compose start qdrant
```

---

## 7. Restore

**Warning:** Restoring overwrites the target collection's current data.
Always take a backup of the current state before restoring.

### Restore from snapshot

```bash
# 1. Copy the snapshot into the container
docker compose cp ./backups/qdrant/<snapshot>.snapshot \
  qdrant:/tmp/<snapshot>.snapshot

# 2. Recover via the Qdrant API
docker compose exec qdrant sh -c \
  'curl -sf -XPOST \
    -H "api-key: $QDRANT__SERVICE__API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"location\": \"file:///tmp/<snapshot>.snapshot\"}" \
    http://localhost:6333/collections/cybotech_document_chunks/snapshots/recover'
```

### Restore from full storage backup

```bash
docker compose down
docker run --rm \
  -v cybotech-agent-security_qdrant_data:/data \
  -v $(pwd)/backups:/backup \
  alpine tar xzf /backup/qdrant-storage-<date>.tar.gz -C /data
docker compose up -d
```

---

## 8. Migration from pgvector

Use the `migrate-vectors` CLI command to export existing PostgreSQL embeddings
into Qdrant. This command is idempotent and resumable.

```bash
# Dry run — inspect counts without writing
cd backend && uv run python -m cybotech.cli.migrate_vectors \
  --db-url "postgresql+psycopg://user:pass@localhost:5432/cybotech" \
  --qdrant-url http://localhost:6333 \
  --qdrant-api-key <key> \
  --dry-run

# Full migration
cd backend && uv run python -m cybotech.cli.migrate_vectors \
  --db-url "postgresql+psycopg://user:pass@localhost:5432/cybotech" \
  --qdrant-url http://localhost:6333 \
  --qdrant-api-key <key>
```

Exit codes: `0` = success, `1` = verified but with discrepancies, `2` = fatal.

**The command never deletes pgvector data.**
Removing pgvector columns requires a separate authorised Alembic migration.

---

## 9. Reconciliation

The reconciliation command detects and repairs missing Qdrant vectors by
comparing PostgreSQL embeddings against Qdrant point IDs.

```bash
# Read-only check
cd backend && uv run python -m cybotech.cli.reconcile_vectors \
  --db-url "postgresql+psycopg://user:pass@localhost:5432/cybotech" \
  --qdrant-url http://localhost:6333 \
  --qdrant-api-key <key>

# Repair missing vectors
cd backend && uv run python -m cybotech.cli.reconcile_vectors \
  --db-url "..." --qdrant-url http://localhost:6333 --qdrant-api-key <key> \
  --repair

# Only check one collection
cd backend && uv run python -m cybotech.cli.reconcile_vectors \
  --collection documents --repair ...
```

Retry the outbox dispatcher for failed sync events:

```python
# In a Python shell or management script:
from cybotech.infrastructure.vector_store.outbox import VectorSyncDispatcher
from cybotech.infrastructure.vector_store.qdrant_adapter import build_qdrant_vector_store
from cybotech.infrastructure.database.session import get_session_factory

with get_session_factory()() as db:
    vs = build_qdrant_vector_store()
    dispatcher = VectorSyncDispatcher(db, vs)
    result = dispatcher.retry_failed()
    print(result)
```

---

## 10. Troubleshooting

### Backend cannot connect to Qdrant

1. Verify Qdrant is running: `docker compose ps qdrant`
2. Check health: `docker compose exec qdrant curl -s http://localhost:6333/healthz`
3. Verify `QDRANT_API_KEY` matches `QDRANT__SERVICE__API_KEY` in the Qdrant container
4. Verify `QDRANT_URL=http://qdrant:6333` (internal Docker hostname, not localhost)

When `QDRANT_URL` is unset the backend falls back to Python-cosine ranking.
All requests will still be served — only vector search degrades.

### Qdrant search returns 0 results after fresh install

Collections may be empty. Run the migration command or re-ingest documents.

### Vector dimension mismatch error

The embedding model dimension has changed. Check `QDRANT_EMBEDDING_DIMENSIONS`
and verify it matches the model output. Do not mix models without a re-embedding run.

### Outbox rows stuck in `pending` or `failed` state

Check `vector_sync_outbox` in PostgreSQL:

```sql
SELECT status, count(*), max(attempt_count), max(created_at)
FROM vector_sync_outbox
GROUP BY status
ORDER BY status;
```

Run the retry command above to re-process failed rows (up to `MAX_ATTEMPTS=10`).
Rows with `attempt_count >= 10` are permanently failed — investigate `last_error`.

### Qdrant disk usage growing unexpectedly

Run reconciliation to check for orphaned points. A point in Qdrant whose
corresponding PostgreSQL record has been deleted is safe to remove.

### After a Qdrant container restart, collections are gone

Verify the `qdrant_data` volume is correctly mounted:

```bash
docker volume inspect cybotech-agent-security_qdrant_data
```

If the volume was accidentally deleted, restore from a snapshot backup and then
run `reconcile_vectors --repair` to rebuild any missing points.

---

## 11. pgvector Removal (Post-Migration, Requires Authorization)

After a successful migration and verification:

1. Run `migrate-vectors` with verification — confirm exit code 0.
2. Run `reconcile-vectors` — confirm 0 missing vectors.
3. Take a full PostgreSQL backup.
4. Create a new Alembic migration to drop `embedding` and `embedding_model`
   columns from `memories` and `document_chunks`.
5. Remove `pgvector` from `pyproject.toml` dependencies.
6. Update `models.py` to remove `Vector` column type.
7. Remove the `CREATE EXTENSION vector` call from migration `20261030_0001`.

**Do not perform step 4-7 without explicit operator authorization.**
The pgvector columns are retained during the migration window as a safety net.
