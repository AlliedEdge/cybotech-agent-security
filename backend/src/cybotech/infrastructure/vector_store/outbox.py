"""
VectorSyncDispatcher — transactional outbox processor for PostgreSQL → Qdrant sync.

The outbox pattern
------------------
1. A canonical record write (document chunk or memory) occurs inside a
   PostgreSQL transaction.
2. In the SAME transaction, a ``VectorSyncOutbox`` row is inserted with
   status="pending", containing the vector and metadata payload.
3. ``dispatch_pending`` is called (typically after commit, or from a scheduled
   task) to process pending rows.
4. For each pending row, the dispatcher calls Qdrant and marks the row
   "completed" on success, or "failed" (retryable) on error.
5. Qdrant downtime: rows remain "pending" and are retried on next call.
6. Repeated delivery is idempotent: Qdrant upsert with the same deterministic
   point ID succeeds without creating duplicates.

Security notes
--------------
- A "pending" row must NEVER be treated as "indexed". The application reads
  Qdrant search results only; it does not read outbox rows for search decisions.
- Failed rows emit a warning log. A monitoring/alerting hook should watch for
  rows with attempt_count > MAX_ATTEMPTS.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cybotech.infrastructure.database.models import (
    VectorSyncOperation,
    VectorSyncOutbox,
    VectorSyncStatus,
)
from cybotech.infrastructure.vector_store.collections import (
    document_collection_config,
    memory_collection_config,
)
from cybotech.infrastructure.vector_store.interface import (
    DocumentChunkPoint,
    MemoryPoint,
    VectorStore,
)

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 10
DISPATCH_BATCH_SIZE = 64


class VectorSyncDispatcher:
    """Processes pending VectorSyncOutbox rows and applies them to Qdrant.

    Designed to be called:
    - Inline after each service commit (for low-latency sync)
    - From a periodic background task (for recovery after Qdrant downtime)
    - From the ``reconcile-vectors`` admin command
    """

    def __init__(self, db: Session, vector_store: VectorStore) -> None:
        self._db = db
        self._vs = vector_store

    def enqueue_upsert_chunk(
        self,
        db: Session,
        *,
        chunk_id: str,
        document_id: str,
        session_id: str,
        content_hash: str,
        embedding_model: str,
        embedding_version: str,
        vector: list[float],
    ) -> VectorSyncOutbox:
        """Create a pending outbox row for a document chunk upsert.

        Must be called WITHIN the same transaction as the DocumentChunk write.
        """
        doc_cfg = document_collection_config()
        row = VectorSyncOutbox(
            collection_name=doc_cfg.name,
            record_id=chunk_id,
            operation=VectorSyncOperation.upsert,
            vector_payload=vector,
            metadata_payload={
                "chunk_id": chunk_id,
                "document_id": document_id,
                "session_id": session_id,
                "content_hash": content_hash,
                "embedding_model": embedding_model,
                "embedding_version": embedding_version,
            },
            status=VectorSyncStatus.pending,
        )
        db.add(row)
        return row

    def enqueue_upsert_memory(
        self,
        db: Session,
        *,
        memory_id: str,
        session_id: str,
        provenance_reference: str,
        content_hash: str,
        embedding_model: str,
        embedding_version: str,
        vector: list[float],
    ) -> VectorSyncOutbox:
        """Create a pending outbox row for a memory upsert.

        Must be called WITHIN the same transaction as the Memory write.
        """
        mem_cfg = memory_collection_config()
        row = VectorSyncOutbox(
            collection_name=mem_cfg.name,
            record_id=memory_id,
            operation=VectorSyncOperation.upsert,
            vector_payload=vector,
            metadata_payload={
                "memory_id": memory_id,
                "session_id": session_id,
                "provenance_reference": provenance_reference,
                "content_hash": content_hash,
                "embedding_model": embedding_model,
                "embedding_version": embedding_version,
            },
            status=VectorSyncStatus.pending,
        )
        db.add(row)
        return row

    def enqueue_delete_chunk(self, db: Session, chunk_id: str) -> VectorSyncOutbox:
        """Create a pending outbox row for a document chunk deletion."""
        doc_cfg = document_collection_config()
        row = VectorSyncOutbox(
            collection_name=doc_cfg.name,
            record_id=chunk_id,
            operation=VectorSyncOperation.delete,
            vector_payload=None,
            metadata_payload=None,
            status=VectorSyncStatus.pending,
        )
        db.add(row)
        return row

    def enqueue_delete_memory(self, db: Session, memory_id: str) -> VectorSyncOutbox:
        """Create a pending outbox row for a memory deletion."""
        mem_cfg = memory_collection_config()
        row = VectorSyncOutbox(
            collection_name=mem_cfg.name,
            record_id=memory_id,
            operation=VectorSyncOperation.delete,
            vector_payload=None,
            metadata_payload=None,
            status=VectorSyncStatus.pending,
        )
        db.add(row)
        return row

    def dispatch_pending(self, *, limit: int = DISPATCH_BATCH_SIZE) -> dict[str, int]:
        """Process pending outbox rows.

        Returns a summary dict: {"processed": N, "succeeded": N, "failed": N}.
        Idempotent and safe to call repeatedly.
        """
        doc_cfg = document_collection_config()
        mem_cfg = memory_collection_config()

        rows = self._db.scalars(
            select(VectorSyncOutbox)
            .where(
                VectorSyncOutbox.status.in_(
                    [VectorSyncStatus.pending, VectorSyncStatus.processing]
                ),
                VectorSyncOutbox.attempt_count < MAX_ATTEMPTS,
            )
            .order_by(VectorSyncOutbox.created_at)
            .limit(limit)
        ).all()

        succeeded = failed = 0
        for row in rows:
            row.status = VectorSyncStatus.processing
            row.attempt_count += 1
            self._db.flush()
            try:
                self._apply(row, doc_cfg.name, mem_cfg.name)
                row.status = VectorSyncStatus.completed
                row.processed_at = datetime.now(UTC)
                row.last_error = None
                succeeded += 1
            except Exception as exc:
                row.status = VectorSyncStatus.failed
                row.last_error = str(exc)[:1000]
                failed += 1
                logger.warning(
                    "Vector sync failed for %s %s (attempt %d): %s",
                    row.collection_name,
                    row.record_id,
                    row.attempt_count,
                    exc,
                )
            self._db.commit()

        return {"processed": len(rows), "succeeded": succeeded, "failed": failed}

    def _apply(self, row: VectorSyncOutbox, doc_collection: str, mem_collection: str) -> None:
        if row.operation == VectorSyncOperation.delete:
            if row.collection_name == doc_collection:
                self._vs.delete_document_chunks([row.record_id])
            else:
                self._vs.delete_memory(row.record_id)
            return

        # Upsert
        meta: dict[str, Any] = row.metadata_payload or {}
        vector: list[float] = row.vector_payload or []

        if row.collection_name == doc_collection:
            self._vs.upsert_document_chunks(
                [
                    DocumentChunkPoint(
                        chunk_id=meta["chunk_id"],
                        document_id=meta["document_id"],
                        session_id=meta["session_id"],
                        content_hash=meta["content_hash"],
                        embedding_model=meta["embedding_model"],
                        embedding_version=meta.get("embedding_version", "1"),
                        vector=vector,
                    )
                ]
            )
        else:
            self._vs.upsert_memory(
                MemoryPoint(
                    memory_id=meta["memory_id"],
                    session_id=meta.get("session_id", ""),
                    provenance_reference=meta.get("provenance_reference", ""),
                    content_hash=meta["content_hash"],
                    embedding_model=meta["embedding_model"],
                    embedding_version=meta.get("embedding_version", "1"),
                    vector=vector,
                )
            )

    def retry_failed(self, *, limit: int = DISPATCH_BATCH_SIZE) -> int:
        """Reset failed-but-retryable rows back to pending and dispatch them.

        Returns the number of rows reset.
        """
        rows = self._db.scalars(
            select(VectorSyncOutbox)
            .where(
                VectorSyncOutbox.status == VectorSyncStatus.failed,
                VectorSyncOutbox.attempt_count < MAX_ATTEMPTS,
            )
            .order_by(VectorSyncOutbox.created_at)
            .limit(limit)
        ).all()
        for row in rows:
            row.status = VectorSyncStatus.pending
        self._db.commit()
        if rows:
            self.dispatch_pending(limit=len(rows))
        return len(rows)
