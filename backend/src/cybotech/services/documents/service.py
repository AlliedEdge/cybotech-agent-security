"""
Document ingestion and RAG retrieval backed by Qdrant vector search.

Ingestion flow:
  1. Extract and normalise text from the document (plain, markdown, email, PDF).
  2. Chunk the text with a sliding window (800 chars, 120 overlap).
  3. Embed every chunk via the configured Embedder.
  4. Persist DocumentRecord + DocumentChunk rows to PostgreSQL
     (text, metadata, audit trail — no vector bytes).
  5. Upsert each chunk vector into Qdrant (cybotech_documents collection)
     with document_id and session_id as filterable payload fields.

Retrieval flow (RAG):
  1. Embed the query.
  2. Qdrant ANN search — filtered to the current session and embedding model.
  3. Hydrate chunk text and document metadata from PostgreSQL using the
     chunk UUIDs returned by Qdrant.
  4. Return DocumentEvidence list ranked by Qdrant cosine score.
"""

from __future__ import annotations

import hashlib
import io
import uuid
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser

from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session

from cybotech.agents.contracts import DocumentEvidence
from cybotech.infrastructure.database.models import AuditEvent, DocumentChunk, DocumentRecord
from cybotech.infrastructure.vector.qdrant import QdrantStore
from cybotech.services.retrieval.service import Embedder

SUPPORTED_TYPES = {"text/plain", "text/markdown", "message/rfc822", "application/pdf"}
MAX_BYTES, CHUNK_SIZE, CHUNK_OVERLAP = 1_000_000, 800, 120


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
    """
    Document ingestion and Qdrant-backed RAG retrieval.

    PostgreSQL stores the document records and chunk text.
    Qdrant stores the chunk embeddings and serves ANN queries.
    """

    def __init__(
        self,
        db: Session,
        embedder: Embedder,
        vector_store: QdrantStore,
        dimensions: int = 384,
    ) -> None:
        self.db = db
        self.embedder = embedder
        self.vector_store = vector_store
        self.dimensions = dimensions

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

        # ── PostgreSQL: document record ───────────────────────────────────
        document = DocumentRecord(
            title=title.strip()[:255],
            document_type=content_type,
            content_hash=content_hash,
            session_id=session_id,
            source_metadata={"ingested": "local_demo"},
        )
        self.db.add(document)
        self.db.flush()  # assigns document.id before we reference it in chunks

        chunk_rows: list[DocumentChunk] = []
        for ordinal, (chunk, _vector) in enumerate(zip(chunks, vectors, strict=True)):
            row = DocumentChunk(
                document_id=document.id,
                ordinal=ordinal,
                content=chunk,
                content_hash=_hash(chunk),
                source_metadata={"document_content_hash": content_hash},
                trust_label="untrusted",
                embedding_model=self.embedder.model_name,
            )
            self.db.add(row)
            chunk_rows.append(row)

        self.db.flush()  # assigns chunk UUIDs before Qdrant upsert

        # ── Qdrant: upsert chunk vectors ──────────────────────────────────
        for row, vector in zip(chunk_rows, vectors, strict=True):
            self.vector_store.upsert_document_chunk(
                chunk_id=str(row.id),
                vector=vector,
                payload={
                    "document_id": str(document.id),
                    "session_id": session_id,
                    "embedding_model": self.embedder.model_name,
                    "trust_label": "untrusted",
                    "ordinal": row.ordinal,
                },
            )

        self._event(
            "document_ingested",
            str(document.id),
            session_id,
            {"chunks": len(chunks), "content_hash": content_hash},
        )
        self.db.commit()
        return document

    def retrieve(self, query: str, session_id: str, top_k: int = 5) -> list[DocumentEvidence]:
        """
        RAG retrieval: Qdrant ANN search → PostgreSQL hydration.

        Qdrant filters by session_id and embedding_model so results are
        scoped to the current session and vector space.  Chunk text and
        document metadata are then fetched from PostgreSQL by chunk UUID.
        """
        if not query.strip() or not 1 <= top_k <= 20:
            return []

        vector = self.embedder.embed(query)
        if len(vector) != self.dimensions:
            raise ValueError(f"embedding dimension must be {self.dimensions}")

        # Step 1 — Qdrant ANN search (session-scoped, model-guarded).
        qdrant_hits = self.vector_store.search_documents(
            vector=vector,
            top_k=top_k,
            filter_payload={
                "session_id": session_id,
                "embedding_model": self.embedder.model_name,
            },
        )
        if not qdrant_hits:
            self._event("document_retrieval", None, session_id, {"count": 0})
            self.db.commit()
            return []

        # Step 2 — hydrate chunk text + document metadata from PostgreSQL.
        chunk_ids = [uuid.UUID(chunk_id) for chunk_id, _score, _payload in qdrant_hits]
        score_map = {chunk_id: score for chunk_id, score, _payload in qdrant_hits}

        rows = list(
            self.db.execute(
                select(DocumentChunk, DocumentRecord)
                .join(DocumentRecord, DocumentChunk.document_id == DocumentRecord.id)
                .where(
                    DocumentChunk.id.in_(chunk_ids),  # type: ignore[arg-type]
                    DocumentRecord.status == "ready",
                    DocumentRecord.deleted_at.is_(None),
                )
            ).all()
        )

        # Preserve Qdrant's cosine-similarity ranking.
        rows.sort(key=lambda r: score_map.get(str(r[0].id), 0.0), reverse=True)

        evidence = [
            DocumentEvidence(
                chunk_id=str(chunk.id),
                document_id=str(document.id),
                content=chunk.content,
                title=document.title,
                source_type=document.document_type,
                content_hash=chunk.content_hash,
            )
            for chunk, document in rows
        ]

        self._event("document_retrieval", None, session_id, {"count": len(evidence)})
        self.db.commit()
        return evidence

    def delete(self, document_id: uuid.UUID, session_id: str) -> bool:
        row = self.db.get(DocumentRecord, document_id)
        if row is None or row.session_id != session_id or row.deleted_at is not None:
            return False
        row.status, row.deleted_at = "deleted", datetime.now(UTC)
        # Remove chunk vectors from Qdrant so they can no longer be retrieved.
        self.vector_store.delete_document_chunks(str(document_id))
        self._event("document_deleted", str(row.id), session_id, {})
        self.db.commit()
        return True

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
