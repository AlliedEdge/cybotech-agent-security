"""
Document ingestion and retrieval service.

Migration note (feat/qdrant-vector-db-migration)
------------------------------------------------
Vector storage and similarity search now route through the ``VectorStore``
abstraction (``infrastructure/vector_store``).  PostgreSQL continues to hold the
canonical ``DocumentRecord`` and ``DocumentChunk`` rows including their
``embedding`` column (kept for the data-migration window; dropped in a separate
authorised migration after verification).

Retrieval flow:
  1. Query is embedded via the injected Embedder.
  2. Qdrant performs ANN search scoped to ``session_id`` and ``embedding_model``.
  3. Qdrant returns (chunk_id, score) pairs.
  4. PostgreSQL fetches the full canonical chunk rows by chunk_id.
  5. Session and deletion guards are re-validated in PostgreSQL — Qdrant
     metadata is NEVER trusted for authorization.
  6. Results are returned as ``DocumentEvidence`` (trust_label="untrusted").

Fallback: when no VectorStore is injected (unit tests using SQLite), the service
falls back to the original Python-side cosine ranking so existing tests continue
to pass without change.
"""

from __future__ import annotations

import hashlib
import io
import logging
import math
import uuid
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from typing import TYPE_CHECKING

from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session

from cybotech.agents.contracts import DocumentEvidence
from cybotech.infrastructure.database.models import AuditEvent, DocumentChunk, DocumentRecord
from cybotech.services.retrieval.service import Embedder

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from cybotech.infrastructure.vector_store.interface import VectorStore
    from cybotech.infrastructure.vector_store.outbox import VectorSyncDispatcher

SUPPORTED_TYPES = {"text/plain", "text/markdown", "message/rfc822", "application/pdf"}
MAX_BYTES, CHUNK_SIZE, CHUNK_OVERLAP = 1_000_000, 800, 120

# Embedding version tag stored in Qdrant payload.  Bump when the embedding
# pipeline changes (e.g. different chunking, normalisation, or model upgrade).
_EMBEDDING_VERSION = "1"


def _hash(value: bytes | str) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def extract_text(data: bytes, content_type: str) -> str:
    if content_type not in SUPPORTED_TYPES:
        raise ValueError("unsupported document type")
    if content_type in {"text/plain", "text/markdown"}:
        return data.decode("utf-8")
    if content_type == "message/rfc822":
        message = BytesParser(policy=policy.default).parsebytes(data)
        return "\n".join(
            part.get_content() for part in message.walk() if part.get_content_type() == "text/plain"
        )
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)


def normalize_text(text: str) -> str:
    return "\n".join(
        line.strip() for line in text.replace("\r\n", "\n").split("\n") if line.strip()
    )


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    if not 0 <= overlap < size:
        raise ValueError("chunk overlap must be smaller than chunk size")
    return [text[start : start + size] for start in range(0, len(text), size - overlap)]


class DocumentService:
    def __init__(
        self,
        db: Session,
        embedder: Embedder,
        dimensions: int = 384,
        vector_store: VectorStore | None = None,
        dispatcher: VectorSyncDispatcher | None = None,
    ) -> None:
        self.db = db
        self.embedder = embedder
        self.dimensions = dimensions
        # Optional: when None the service falls back to Python-side cosine
        # ranking (used by SQLite-based unit tests).
        self._vector_store = vector_store
        self._dispatcher = dispatcher

    # ── Ingestion ──────────────────────────────────────────────────────────

    def ingest(self, title: str, content_type: str, data: bytes, session_id: str) -> DocumentRecord:
        if not title.strip() or not session_id.strip() or not data or len(data) > MAX_BYTES:
            raise ValueError("invalid document input")
        content_hash = _hash(data)
        existing = self.db.scalar(
            select(DocumentRecord).where(DocumentRecord.content_hash == content_hash)
        )
        if existing is not None:
            if existing.session_id != session_id:
                raise ValueError("duplicate document belongs to another session")
            return existing
        text = normalize_text(extract_text(data, content_type))
        if not text:
            raise ValueError("document has no extractable text")
        chunks = chunk_text(text)
        vectors = [self.embedder.embed(chunk) for chunk in chunks]
        if any(len(v) != self.dimensions for v in vectors):
            raise ValueError(f"embedding dimension must be {self.dimensions}")
        document = DocumentRecord(
            title=title.strip()[:255],
            document_type=content_type,
            content_hash=content_hash,
            session_id=session_id,
            source_metadata={"ingested": "local_demo"},
        )
        self.db.add(document)
        self.db.flush()

        chunk_records: list[DocumentChunk] = []
        for ordinal, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
            chunk_hash = _hash(chunk)
            db_chunk = DocumentChunk(
                document_id=document.id,
                ordinal=ordinal,
                content=chunk,
                content_hash=chunk_hash,
                source_metadata={"document_content_hash": content_hash},
                trust_label="untrusted",
                embedding=vector,
                embedding_model=self.embedder.model_name,
            )
            self.db.add(db_chunk)
            chunk_records.append(db_chunk)

        self.db.flush()  # assign UUIDs to all chunks before outbox entries

        # Enqueue Qdrant upserts WITHIN the same PostgreSQL transaction.
        if self._dispatcher is not None:
            for db_chunk, vector in zip(chunk_records, vectors, strict=True):
                self._dispatcher.enqueue_upsert_chunk(
                    self.db,
                    chunk_id=str(db_chunk.id),
                    document_id=str(document.id),
                    session_id=session_id,
                    content_hash=db_chunk.content_hash,
                    embedding_model=self.embedder.model_name,
                    embedding_version=_EMBEDDING_VERSION,
                    vector=vector,
                )

        self._event(
            "document_ingested",
            str(document.id),
            session_id,
            {"chunks": len(chunks), "content_hash": content_hash},
        )
        self.db.commit()

        # Dispatch outbox events after the PostgreSQL commit so Qdrant only
        # receives data that is durably stored.  Failures leave rows in
        # "pending" state for retry; they do NOT roll back the document write.
        if self._dispatcher is not None:
            try:
                self._dispatcher.dispatch_pending()
            except Exception:
                # Qdrant being down must not prevent document ingestion.
                # The outbox guarantees eventual delivery.
                logger.debug("Outbox dispatch failed after ingest", exc_info=True)

        return document

    # ── Retrieval ──────────────────────────────────────────────────────────

    def retrieve(self, query: str, session_id: str, top_k: int = 5) -> list[DocumentEvidence]:
        if not query.strip() or not 1 <= top_k <= 20:
            return []
        vector = self.embedder.embed(query)
        if len(vector) != self.dimensions:
            raise ValueError(f"embedding dimension must be {self.dimensions}")

        if self._vector_store is not None:
            evidence = self._retrieve_via_qdrant(vector, session_id, top_k)
        else:
            evidence = self._retrieve_python_fallback(vector, session_id, top_k)

        self._event("document_retrieval", None, session_id, {"count": len(evidence)})
        self.db.commit()
        return evidence

    def _retrieve_via_qdrant(
        self,
        query_vector: list[float],
        session_id: str,
        top_k: int,
    ) -> list[DocumentEvidence]:
        """ANN search via Qdrant, canonical record hydration via PostgreSQL.

        Security: Qdrant payload values are not used for authorization.
        The canonical PostgreSQL records are re-fetched and re-validated.
        """
        assert self._vector_store is not None
        hits = self._vector_store.search_document_chunks(
            query_vector=query_vector,
            session_id=session_id,
            embedding_model=self.embedder.model_name,
            top_k=top_k,
        )
        if not hits:
            return []

        chunk_ids = [uuid.UUID(h.point_id) for h in hits]
        # Re-fetch canonical rows from PostgreSQL — this is the authorization gate.
        rows = self.db.execute(
            select(DocumentChunk, DocumentRecord)
            .join(DocumentRecord, DocumentChunk.document_id == DocumentRecord.id)
            .where(
                DocumentChunk.id.in_(chunk_ids),
                # Re-validate session scope in PostgreSQL — never rely on Qdrant payload.
                DocumentRecord.session_id == session_id,
                DocumentRecord.status == "ready",
                DocumentRecord.deleted_at.is_(None),
                DocumentChunk.embedding_model == self.embedder.model_name,
            )
        ).all()

        # Build a lookup so we can preserve Qdrant's relevance order.
        row_map = {str(chunk.id): (chunk, document) for chunk, document in rows}
        evidence = []
        for hit in hits:
            pair = row_map.get(hit.point_id)
            if pair is None:
                # Qdrant returned a chunk that failed the PostgreSQL revalidation
                # (wrong session, deleted, etc.).  Silently exclude it.
                continue
            chunk, document = pair
            evidence.append(
                DocumentEvidence(
                    chunk_id=str(chunk.id),
                    document_id=str(document.id),
                    content=chunk.content,
                    title=document.title,
                    source_type=document.document_type,
                    content_hash=chunk.content_hash,
                )
            )
        return evidence

    def _retrieve_python_fallback(
        self,
        query_vector: list[float],
        session_id: str,
        top_k: int,
    ) -> list[DocumentEvidence]:
        """Original Python-side cosine ranking — used when Qdrant is unavailable.

        Kept for: SQLite-based unit tests, and as a recovery path if Qdrant
        is temporarily unreachable during retrieval.
        """
        rows = self.db.execute(
            select(DocumentChunk, DocumentRecord)
            .join(DocumentRecord, DocumentChunk.document_id == DocumentRecord.id)
            .where(
                DocumentRecord.session_id == session_id,
                DocumentRecord.status == "ready",
                DocumentRecord.deleted_at.is_(None),
                DocumentChunk.embedding_model == self.embedder.model_name,
            )
        ).all()
        ranked = sorted(rows, key=lambda row: _distance(row[0].embedding, query_vector))[:top_k]
        return [
            DocumentEvidence(
                chunk_id=str(chunk.id),
                document_id=str(document.id),
                content=chunk.content,
                title=document.title,
                source_type=document.document_type,
                content_hash=chunk.content_hash,
            )
            for chunk, document in ranked
        ]

    # ── Deletion ──────────────────────────────────────────────────────────

    def delete(self, document_id: uuid.UUID, session_id: str) -> bool:
        row = self.db.get(DocumentRecord, document_id)
        if row is None or row.session_id != session_id or row.deleted_at is not None:
            return False
        row.status, row.deleted_at = "deleted", datetime.now(UTC)

        # Enqueue Qdrant deletions for all chunks of this document.
        if self._dispatcher is not None:
            chunk_ids = self.db.scalars(
                select(DocumentChunk.id).where(DocumentChunk.document_id == document_id)
            ).all()
            for chunk_id in chunk_ids:
                self._dispatcher.enqueue_delete_chunk(self.db, str(chunk_id))

        self._event("document_deleted", str(row.id), session_id, {})
        self.db.commit()

        if self._dispatcher is not None:
            try:
                self._dispatcher.dispatch_pending()
            except Exception:
                logger.debug("Outbox dispatch failed after ingest", exc_info=True)

        return True

    # ── Internal helpers ───────────────────────────────────────────────────

    def _event(
        self,
        event_type: str,
        object_id: str | None,
        session_id: str,
        payload: dict[str, object],
    ) -> None:
        self.db.add(
            AuditEvent(
                event_id=f"evt_{uuid.uuid4().hex}",
                event_type=event_type,
                actor="document_service",
                correlation_id=session_id,
                object_id=object_id,
                outcome="recorded",
                payload=payload,
            )
        )


def _distance(left: list[float], right: list[float]) -> float:
    """Cosine distance for the Python-side fallback ranker."""
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    return 1 - dot / (
        (math.sqrt(sum(a * a for a in left)) or 1) * (math.sqrt(sum(b * b for b in right)) or 1)
    )
