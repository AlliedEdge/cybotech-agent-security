"""Local-demo APIs for untrusted document evidence."""

from __future__ import annotations

import base64
import binascii
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from cybotech.api.v1.security import require_local_security_access, runtime_embedder
from cybotech.infrastructure.database.models import DocumentChunk, DocumentRecord
from cybotech.infrastructure.database.session import get_db
from cybotech.services.documents.service import MAX_BYTES, SUPPORTED_TYPES, DocumentService

DbSession = Annotated[Session, Depends(get_db)]
router = APIRouter(dependencies=[Depends(require_local_security_access)])


class IngestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=255)
    content_type: str
    content_base64: str = Field(min_length=1)
    session_id: str = Field(min_length=1, max_length=120)


class RetrieveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=8000)
    session_id: str = Field(min_length=1, max_length=120)
    top_k: int = Field(default=5, ge=1, le=20)


def _document(row: DocumentRecord) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "title": row.title,
        "type": row.document_type,
        "content_hash": row.content_hash,
        "session_id": row.session_id,
        "status": row.status,
        "created_at": row.created_at,
    }


@router.post("/documents/ingest")
def ingest(body: IngestIn, db: DbSession) -> dict[str, Any]:
    if body.content_type not in SUPPORTED_TYPES:
        raise HTTPException(422, "Unsupported document type")
    try:
        data = base64.b64decode(body.content_base64, validate=True)
    except binascii.Error:
        raise HTTPException(422, "content_base64 is invalid") from None
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Document exceeds 1MB limit")
    try:
        row = DocumentService(db, runtime_embedder()).ingest(
            body.title, body.content_type, data, body.session_id
        )
    except (UnicodeDecodeError, ValueError, OSError) as error:
        raise HTTPException(422, str(error)) from None
    return _document(row)


@router.get("/documents")
def list_documents(session_id: str, db: DbSession) -> dict[str, Any]:
    rows = db.scalars(
        select(DocumentRecord).where(
            DocumentRecord.session_id == session_id, DocumentRecord.deleted_at.is_(None)
        )
    ).all()
    return {"items": [_document(row) for row in rows]}


@router.get("/documents/{document_id}")
def get_document(document_id: uuid.UUID, session_id: str, db: DbSession) -> dict[str, Any]:
    row = db.get(DocumentRecord, document_id)
    if row is None or row.session_id != session_id or row.deleted_at is not None:
        raise HTTPException(404, "Document not found")
    return _document(row)


@router.get("/documents/{document_id}/chunks")
def chunks(document_id: uuid.UUID, session_id: str, db: DbSession) -> dict[str, Any]:
    row = db.get(DocumentRecord, document_id)
    if row is None or row.session_id != session_id or row.deleted_at is not None:
        raise HTTPException(404, "Document not found")
    rows = db.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.ordinal)
    ).all()
    return {
        "items": [
            {
                "id": str(chunk.id),
                "ordinal": chunk.ordinal,
                "content": chunk.content,
                "content_hash": chunk.content_hash,
                "trust_label": chunk.trust_label,
            }
            for chunk in rows
        ]
    }


@router.post("/documents/retrieve")
def retrieve(body: RetrieveIn, db: DbSession) -> dict[str, Any]:
    rows = DocumentService(db, runtime_embedder()).retrieve(body.query, body.session_id, body.top_k)
    return {"items": [row.model_dump() for row in rows]}


@router.delete("/documents/{document_id}")
def delete_document(document_id: uuid.UUID, session_id: str, db: DbSession) -> dict[str, bool]:
    if not DocumentService(db, runtime_embedder()).delete(document_id, session_id):
        raise HTTPException(404, "Document not found")
    return {"deleted": True}
