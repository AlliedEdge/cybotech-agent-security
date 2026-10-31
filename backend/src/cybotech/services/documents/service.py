from __future__ import annotations

import hashlib
import io
import math
import uuid
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser

from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session

from cybotech.agents.contracts import DocumentEvidence
from cybotech.infrastructure.database.models import AuditEvent, DocumentChunk, DocumentRecord
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
    def __init__(self, db: Session, embedder: Embedder, dimensions: int = 384) -> None:
        self.db, self.embedder, self.dimensions = db, embedder, dimensions

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
        if any(len(vector) != self.dimensions for vector in vectors):
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
        for ordinal, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
            self.db.add(
                DocumentChunk(
                    document_id=document.id,
                    ordinal=ordinal,
                    content=chunk,
                    content_hash=_hash(chunk),
                    source_metadata={"document_content_hash": content_hash},
                    trust_label="untrusted",
                    embedding=vector,
                    embedding_model=self.embedder.model_name,
                )
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
        if not query.strip() or not 1 <= top_k <= 20:
            return []
        vector = self.embedder.embed(query)
        if len(vector) != self.dimensions:
            raise ValueError(f"embedding dimension must be {self.dimensions}")
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
        ranked = sorted(rows, key=lambda row: _distance(row[0].embedding, vector))[:top_k]
        evidence = [
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
        self._event("document_retrieval", None, session_id, {"count": len(evidence)})
        self.db.commit()
        return evidence

    def delete(self, document_id: uuid.UUID, session_id: str) -> bool:
        row = self.db.get(DocumentRecord, document_id)
        if row is None or row.session_id != session_id or row.deleted_at is not None:
            return False
        row.status, row.deleted_at = "deleted", datetime.now(UTC)
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


def _distance(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    return 1 - dot / (
        (math.sqrt(sum(a * a for a in left)) or 1) * (math.sqrt(sum(b * b for b in right)) or 1)
    )
