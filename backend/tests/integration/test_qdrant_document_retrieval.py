"""
End-to-end Qdrant document retrieval integration tests.

Tests the full pipeline:
  ingest (DocumentService) → Qdrant outbox dispatch → search → PostgreSQL revalidation

Requires CYBOTECH_TEST_QDRANT_URL. Uses throwaway SQLite + throwaway Qdrant collections.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from cybotech.infrastructure.database.models import Base
from cybotech.infrastructure.vector_store.collections import CollectionConfig
from cybotech.infrastructure.vector_store.outbox import VectorSyncDispatcher
from cybotech.infrastructure.vector_store.qdrant_adapter import QdrantVectorStore
from cybotech.services.documents.service import DocumentService

QDRANT_URL = os.getenv("CYBOTECH_TEST_QDRANT_URL")
QDRANT_API_KEY = os.getenv("CYBOTECH_TEST_QDRANT_API_KEY") or None

pytestmark = pytest.mark.skipif(
    not QDRANT_URL,
    reason="requires CYBOTECH_TEST_QDRANT_URL",
)

MODEL = "text-embedding-3-small"
DIM = 384
_RUN_ID = uuid.uuid4().hex[:8]

DOC_COL = f"cybotech_test_{_RUN_ID}_doc_e2e"
MEM_COL = f"cybotech_test_{_RUN_ID}_mem_e2e"


class FakeEmbedder:
    """Embeds using a simple term-frequency hash so 'schedule' chunks are clearly similar."""

    model_name = MODEL

    def embed(self, text: str) -> list[float]:
        v = [0.0] * DIM
        words = text.lower().split()
        for word in words:
            idx = hash(word) % DIM
            v[idx] += 1.0
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / norm for x in v]


@pytest.fixture(scope="module")
def qdrant_client():
    from qdrant_client import QdrantClient

    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=10)
    yield client
    try:
        for col in client.get_collections().collections:
            if col.name.startswith(f"cybotech_test_{_RUN_ID}"):
                client.delete_collection(col.name)
    except Exception:
        pass


@pytest.fixture
def db_and_store(qdrant_client):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        doc_cfg = CollectionConfig(
            name=DOC_COL,
            dimension=DIM,
            embedding_model=MODEL,
            distance="Cosine",
            indexed_payload_fields=("session_id", "embedding_model"),
        )
        mem_cfg = CollectionConfig(
            name=MEM_COL,
            dimension=DIM,
            embedding_model=MODEL,
            distance="Cosine",
            indexed_payload_fields=("embedding_model",),
        )
        vs = QdrantVectorStore(client=qdrant_client, document_config=doc_cfg, memory_config=mem_cfg)
        vs.ensure_collections()
        dispatcher = VectorSyncDispatcher(db, vs)
        yield db, vs, dispatcher
    engine.dispose()


def test_ingest_indexes_in_qdrant_and_retrieval_returns_evidence(db_and_store):
    """Full pipeline: ingest → Qdrant sync → query → DocumentEvidence returned."""
    db, vs, dispatcher = db_and_store
    embedder = FakeEmbedder()
    service = DocumentService(db, embedder, vector_store=vs, dispatcher=dispatcher)

    content = b"Approved synthetic delivery schedule for Q4. Maintenance window Friday."
    doc = service.ingest("schedule.txt", "text/plain", content, session_id="pipe-session-a")

    # Verify PostgreSQL canonical record
    assert doc.session_id == "pipe-session-a"

    # Verify Qdrant has the vectors
    results = vs.search_document_chunks(
        embedder.embed("delivery schedule"),
        session_id="pipe-session-a",
        embedding_model=MODEL,
        top_k=5,
    )
    assert len(results) > 0

    # Verify full retrieval pipeline
    evidence = service.retrieve("delivery schedule", "pipe-session-a")
    assert len(evidence) > 0
    assert all(e.trust_label == "untrusted" for e in evidence)


def test_session_isolation_in_full_pipeline(db_and_store):
    """Documents from session A must not appear in session B searches."""
    db, vs, dispatcher = db_and_store
    embedder = FakeEmbedder()
    service = DocumentService(db, embedder, vector_store=vs, dispatcher=dispatcher)

    service.ingest("doc_a.txt", "text/plain", b"Secret project alpha data.", session_id="iso-a")
    service.ingest("doc_b.txt", "text/plain", b"Secret project beta data.", session_id="iso-b")

    evidence_a = service.retrieve("project data", "iso-a")
    evidence_b = service.retrieve("project data", "iso-b")

    ids_a = {e.chunk_id for e in evidence_a}
    ids_b = {e.chunk_id for e in evidence_b}
    assert ids_a.isdisjoint(ids_b), "Session A and B must not share evidence"


def test_deletion_removes_from_qdrant(db_and_store):
    """After soft-deletion PostgreSQL revalidation excludes the document."""
    db, vs, dispatcher = db_and_store
    embedder = FakeEmbedder()
    service = DocumentService(db, embedder, vector_store=vs, dispatcher=dispatcher)

    doc = service.ingest(
        "del_doc.txt", "text/plain", b"Temporary vendor note to delete.", session_id="del-s"
    )

    # Verify reachable
    before = service.retrieve("vendor note", "del-s")
    assert len(before) > 0

    deleted = service.delete(doc.id, "del-s")
    assert deleted is True

    # Verify PostgreSQL revalidation excludes deleted document
    after = service.retrieve("vendor note", "del-s")
    ids_after = {e.chunk_id for e in after}
    ids_before = {e.chunk_id for e in before}
    assert ids_before.isdisjoint(ids_after)


def test_qdrant_revalidation_excludes_wrong_session(db_and_store):
    """If Qdrant returns a chunk from wrong session, PostgreSQL revalidation removes it."""
    db, vs, dispatcher = db_and_store
    embedder = FakeEmbedder()
    service = DocumentService(db, embedder, vector_store=vs, dispatcher=dispatcher)

    # Ingest in session X
    service.ingest(
        "xsession.txt", "text/plain", b"Cross-session test content.", session_id="xsession-x"
    )

    # Search from session Y — must return nothing (PostgreSQL filter)
    evidence = service.retrieve("cross-session", "xsession-y")
    assert evidence == []
