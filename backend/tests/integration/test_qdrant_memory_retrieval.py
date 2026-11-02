"""
End-to-end Qdrant memory retrieval integration tests.

Tests the full pipeline:
  propose_memory → PostgreSQL canonical record → Qdrant outbox → search candidates
  → PostgreSQL revalidation (is_eligible) → only trusted memories returned

Security invariant tests:
- Revoked memory must not be returned even if Qdrant still has its vector
- Quarantined memory must not be returned
- PostgreSQL trust state is authoritative — Qdrant metadata never grants trust

Requires CYBOTECH_TEST_QDRANT_URL. Uses SQLite + throwaway Qdrant collections.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from cybotech.infrastructure.database.models import Base, Memory, MemoryStatus
from cybotech.infrastructure.vector_store.collections import CollectionConfig
from cybotech.infrastructure.vector_store.outbox import VectorSyncDispatcher
from cybotech.infrastructure.vector_store.qdrant_adapter import QdrantVectorStore
from cybotech.services.memory_guard.service import MemoryGuard
from cybotech.services.retrieval.service import RetrievalService, is_eligible

QDRANT_URL = os.getenv("CYBOTECH_TEST_QDRANT_URL")
QDRANT_API_KEY = os.getenv("CYBOTECH_TEST_QDRANT_API_KEY") or None

pytestmark = pytest.mark.skipif(
    not QDRANT_URL,
    reason="requires CYBOTECH_TEST_QDRANT_URL",
)

MODEL = "text-embedding-3-small"
DIM = 384
_RUN_ID = uuid.uuid4().hex[:8]

DOC_COL = f"cybotech_test_{_RUN_ID}_doc_mem"
MEM_COL = f"cybotech_test_{_RUN_ID}_mem_mem"


class FakeEmbedder:
    model_name = MODEL

    def embed(self, text: str) -> list[float]:
        v = [0.0] * DIM
        for word in text.lower().split():
            v[hash(word) % DIM] += 1.0
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
def env(qdrant_client):
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
        embedder = FakeEmbedder()
        yield db, vs, dispatcher, embedder
    engine.dispose()


def _trusted_provenance(source_id: str = "internal-maintenance-v1") -> dict:
    return {
        "source_id": source_id,
        "source_type": "internal",
        "trust_level": "verified",
        "content_sha256": "abc",
        "review_verified": True,
    }


def _add_trusted_memory(db: Session, vs, dispatcher, embedder, content: str) -> Memory:
    """Insert a trusted Memory record and upsert its vector to Qdrant directly."""
    from cybotech.infrastructure.vector_store.interface import MemoryPoint

    mid = uuid.uuid4()
    key = uuid.uuid4().hex
    vector = embedder.embed(content)
    row = Memory(
        id=mid,
        content=content,
        category="fact",
        status=MemoryStatus.trusted,
        source_type="internal",
        source_id="internal-maintenance-v1",
        provenance=_trusted_provenance(),
        created_by="test",
        proposal_key=key,
        decision_reason="approved",
        embedding=vector,
        embedding_model=MODEL,
    )
    db.add(row)
    db.commit()
    # Directly upsert to Qdrant (simulating what the outbox dispatcher does)
    vs.upsert_memory(
        MemoryPoint(
            memory_id=str(mid),
            session_id="",
            provenance_reference="internal-maintenance-v1",
            content_hash="test",
            embedding_model=MODEL,
            embedding_version="1",
            vector=vector,
        )
    )
    return row


class TestMemoryRetrieval:
    def test_trusted_memory_returned(self, env):
        db, vs, dispatcher, embedder = env
        row = _add_trusted_memory(db, vs, dispatcher, embedder, "Vendor maintenance Friday 18 UTC")

        service = RetrievalService(db, embedder, vector_store=vs)
        results = service.retrieve("maintenance schedule", principal="primary_assistant")
        ids = [str(r.id) for r in results]
        assert str(row.id) in ids

    def test_quarantined_memory_not_returned(self, env):
        """Quarantined memory must be excluded by is_eligible() even if in Qdrant."""
        db, vs, _dispatcher, embedder = env
        from cybotech.infrastructure.vector_store.interface import MemoryPoint

        mid = uuid.uuid4()
        vector = embedder.embed("quarantined sensitive content")
        row = Memory(
            id=mid,
            content="quarantined sensitive content",
            category="fact",
            status=MemoryStatus.quarantined,  # NOT trusted
            source_type="email",
            source_id="untrusted-src",
            provenance={"trust_level": "untrusted"},
            created_by="test",
            proposal_key=uuid.uuid4().hex,
            decision_reason="quarantined",
            embedding=vector,
            embedding_model=MODEL,
        )
        db.add(row)
        db.commit()
        # Force-upsert to Qdrant (simulates a scenario where vector exists but record is untrusted)
        vs.upsert_memory(
            MemoryPoint(
                memory_id=str(mid),
                session_id="",
                provenance_reference="",
                content_hash="qh",
                embedding_model=MODEL,
                embedding_version="1",
                vector=vector,
            )
        )

        service = RetrievalService(db, embedder, vector_store=vs)
        results = service.retrieve("quarantined sensitive", principal="primary_assistant")
        ids = [str(r.id) for r in results]
        assert str(mid) not in ids, "Quarantined memory must be excluded by is_eligible()"

    def test_revoked_memory_not_returned_after_deletion(self, env):
        """After revocation the vector is deleted from Qdrant — must not appear."""
        db, vs, dispatcher, embedder = env
        row = _add_trusted_memory(db, vs, dispatcher, embedder, "Revocation test content here")

        # Revoke via MemoryGuard (which enqueues delete to Qdrant)
        guard = MemoryGuard(db, embedder=embedder, dispatcher=dispatcher)
        guard.transition(row.id, MemoryStatus.revoked, "test-reviewer", "test-revocation")

        service = RetrievalService(db, embedder, vector_store=vs)
        results = service.retrieve("revocation test", principal="primary_assistant")
        ids = [str(r.id) for r in results]
        assert str(row.id) not in ids, "Revoked memory must not appear in retrieval"

    def test_is_eligible_is_the_trust_gate(self, env):
        """Even if Qdrant returns an ID, is_eligible() must reject non-trusted records."""
        db, vs, _dispatcher, embedder = env
        from cybotech.infrastructure.vector_store.interface import MemoryPoint

        mid = uuid.uuid4()
        vector = embedder.embed("pending review content")
        row = Memory(
            id=mid,
            content="pending review content",
            category="fact",
            status=MemoryStatus.pending,  # NOT trusted
            source_type="internal",
            source_id="internal-maintenance-v1",
            provenance=_trusted_provenance(),
            created_by="test",
            proposal_key=uuid.uuid4().hex,
            decision_reason="review_required",
            embedding=vector,
            embedding_model=MODEL,
        )
        db.add(row)
        db.commit()
        # Force into Qdrant to test that trust gate works regardless
        vs.upsert_memory(
            MemoryPoint(
                memory_id=str(mid),
                session_id="",
                provenance_reference="",
                content_hash="ph",
                embedding_model=MODEL,
                embedding_version="1",
                vector=vector,
            )
        )

        assert not is_eligible(row), "is_eligible() must reject pending memory"

        service = RetrievalService(db, embedder, vector_store=vs)
        results = service.retrieve("pending review", principal="primary_assistant")
        ids = [str(r.id) for r in results]
        assert str(mid) not in ids, "Pending memory must be rejected by is_eligible()"
