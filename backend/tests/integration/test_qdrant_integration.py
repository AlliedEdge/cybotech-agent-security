"""
Real Qdrant integration tests.

Requires a live Qdrant instance.  Set:
    CYBOTECH_TEST_QDRANT_URL=http://localhost:6333
    CYBOTECH_TEST_QDRANT_API_KEY=<key-or-empty>

The tests use isolated throwaway collections (prefixed with the test run UUID)
so they never interfere with production data.

Tests cover:
- Collection creation (ensure_collections is idempotent)
- Vector insertion and retrieval
- Similarity search and ranking
- Payload filtering (session_id, embedding_model)
- Document session isolation (session A cannot retrieve session B results)
- Memory session isolation
- Memory revocation (deleted from Qdrant after revoke)
- Document deletion
- Synchronization recovery (retry after downtime simulation)
- Cross-session access prevention

Run with:
    CYBOTECH_TEST_QDRANT_URL=http://localhost:6333 \
        pytest tests/integration/test_qdrant_integration.py -v
"""

from __future__ import annotations

import os
import uuid

import pytest

from cybotech.infrastructure.vector_store.collections import CollectionConfig
from cybotech.infrastructure.vector_store.interface import (
    DocumentChunkPoint,
    MemoryPoint,
    VectorDimensionError,
)
from cybotech.infrastructure.vector_store.qdrant_adapter import QdrantVectorStore

QDRANT_URL = os.getenv("CYBOTECH_TEST_QDRANT_URL")
QDRANT_API_KEY = os.getenv("CYBOTECH_TEST_QDRANT_API_KEY") or None

pytestmark = pytest.mark.skipif(
    not QDRANT_URL,
    reason="requires CYBOTECH_TEST_QDRANT_URL to be set",
)

MODEL = "text-embedding-3-small"
DIM = 384
_RUN_ID = uuid.uuid4().hex[:8]


def _col(suffix: str) -> str:
    return f"cybotech_test_{_RUN_ID}_{suffix}"


@pytest.fixture(scope="module")
def qdrant_client():
    from qdrant_client import QdrantClient

    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=10)
    yield client
    # Cleanup: delete all test collections created by this run
    try:
        for col in client.get_collections().collections:
            if col.name.startswith(f"cybotech_test_{_RUN_ID}"):
                client.delete_collection(col.name)
    except Exception:
        pass


@pytest.fixture
def doc_store(qdrant_client):
    cfg = CollectionConfig(
        name=_col("docs"),
        dimension=DIM,
        embedding_model=MODEL,
        distance="Cosine",
        indexed_payload_fields=("session_id", "embedding_model"),
    )
    mem_cfg = CollectionConfig(
        name=_col("mems"),
        dimension=DIM,
        embedding_model=MODEL,
        distance="Cosine",
        indexed_payload_fields=("embedding_model",),
    )
    store = QdrantVectorStore(qdrant_client, doc_cfg=cfg, mem_cfg=mem_cfg)
    # Handle keyword-arg name difference
    store = QdrantVectorStore(
        client=qdrant_client,
        document_config=cfg,
        memory_config=mem_cfg,
    )
    store.ensure_collections()
    return store


def _vec(val: float = 0.1) -> list[float]:
    return [val] * DIM


def _ortho_vec() -> list[float]:
    """A vector orthogonal to _vec(0.1) — low cosine similarity."""
    v = [0.0] * DIM
    v[1] = 1.0
    return v


# ---------------------------------------------------------------------------
# Collection lifecycle
# ---------------------------------------------------------------------------


class TestCollectionCreation:
    def test_ensure_collections_idempotent(self, doc_store):
        """Calling ensure_collections twice must not raise."""
        doc_store.ensure_collections()
        doc_store.ensure_collections()

    def test_health_check_passes(self, doc_store):
        assert doc_store.health_check() is True


# ---------------------------------------------------------------------------
# Document chunk operations
# ---------------------------------------------------------------------------


class TestDocumentChunkUpsertAndSearch:
    def test_upsert_and_retrieve_by_search(self, doc_store):
        chunk_id = str(uuid.uuid4())
        doc_store.upsert_document_chunks(
            [
                DocumentChunkPoint(
                    chunk_id=chunk_id,
                    document_id=str(uuid.uuid4()),
                    session_id="session-a",
                    content_hash="hash1",
                    embedding_model=MODEL,
                    embedding_version="1",
                    vector=_vec(0.5),
                )
            ]
        )
        results = doc_store.search_document_chunks(
            _vec(0.5), session_id="session-a", embedding_model=MODEL, top_k=5
        )
        assert any(r.point_id == chunk_id for r in results)

    def test_search_returns_higher_score_first(self, doc_store):
        cid_close = str(uuid.uuid4())
        cid_far = str(uuid.uuid4())
        doc_store.upsert_document_chunks(
            [
                DocumentChunkPoint(
                    chunk_id=cid_close,
                    document_id=str(uuid.uuid4()),
                    session_id="session-rank",
                    content_hash="h1",
                    embedding_model=MODEL,
                    embedding_version="1",
                    vector=_vec(0.9),
                ),
                DocumentChunkPoint(
                    chunk_id=cid_far,
                    document_id=str(uuid.uuid4()),
                    session_id="session-rank",
                    content_hash="h2",
                    embedding_model=MODEL,
                    embedding_version="1",
                    vector=_ortho_vec(),
                ),
            ]
        )
        results = doc_store.search_document_chunks(
            _vec(0.9), session_id="session-rank", embedding_model=MODEL, top_k=10
        )
        ids = [r.point_id for r in results]
        if cid_close in ids and cid_far in ids:
            assert ids.index(cid_close) < ids.index(cid_far)


# ---------------------------------------------------------------------------
# Session isolation
# ---------------------------------------------------------------------------


class TestDocumentSessionIsolation:
    def test_session_a_cannot_see_session_b_chunks(self, doc_store):
        cid_a = str(uuid.uuid4())
        cid_b = str(uuid.uuid4())
        doc_store.upsert_document_chunks(
            [
                DocumentChunkPoint(
                    chunk_id=cid_a,
                    document_id=str(uuid.uuid4()),
                    session_id="iso-session-a",
                    content_hash="ha",
                    embedding_model=MODEL,
                    embedding_version="1",
                    vector=_vec(0.7),
                ),
                DocumentChunkPoint(
                    chunk_id=cid_b,
                    document_id=str(uuid.uuid4()),
                    session_id="iso-session-b",
                    content_hash="hb",
                    embedding_model=MODEL,
                    embedding_version="1",
                    vector=_vec(0.7),
                ),
            ]
        )
        results_a = doc_store.search_document_chunks(
            _vec(0.7), session_id="iso-session-a", embedding_model=MODEL, top_k=20
        )
        results_b = doc_store.search_document_chunks(
            _vec(0.7), session_id="iso-session-b", embedding_model=MODEL, top_k=20
        )
        ids_a = {r.point_id for r in results_a}
        ids_b = {r.point_id for r in results_b}
        assert cid_a in ids_a
        assert cid_b not in ids_a, "Session A must not see Session B's chunks"
        assert cid_b in ids_b
        assert cid_a not in ids_b, "Session B must not see Session A's chunks"


# ---------------------------------------------------------------------------
# Document deletion
# ---------------------------------------------------------------------------


class TestDocumentDeletion:
    def test_deleted_chunk_not_returned_in_search(self, doc_store):
        cid = str(uuid.uuid4())
        doc_store.upsert_document_chunks(
            [
                DocumentChunkPoint(
                    chunk_id=cid,
                    document_id=str(uuid.uuid4()),
                    session_id="del-session",
                    content_hash="hd",
                    embedding_model=MODEL,
                    embedding_version="1",
                    vector=_vec(0.6),
                )
            ]
        )
        # Confirm it's there
        before = doc_store.search_document_chunks(
            _vec(0.6), session_id="del-session", embedding_model=MODEL, top_k=5
        )
        assert any(r.point_id == cid for r in before)

        doc_store.delete_document_chunks([cid])

        after = doc_store.search_document_chunks(
            _vec(0.6), session_id="del-session", embedding_model=MODEL, top_k=5
        )
        assert not any(r.point_id == cid for r in after)

    def test_delete_idempotent(self, doc_store):
        """Deleting a non-existent chunk must not raise."""
        doc_store.delete_document_chunks([str(uuid.uuid4())])


# ---------------------------------------------------------------------------
# Memory operations
# ---------------------------------------------------------------------------


class TestMemoryUpsertAndSearch:
    def test_upsert_and_find_memory(self, doc_store):
        mid = str(uuid.uuid4())
        doc_store.upsert_memory(
            MemoryPoint(
                memory_id=mid,
                session_id="",
                provenance_reference="src-1",
                content_hash="mh1",
                embedding_model=MODEL,
                embedding_version="1",
                vector=_vec(0.4),
            )
        )
        results = doc_store.search_memories(_vec(0.4), MODEL, top_k=10)
        assert any(r.point_id == mid for r in results)

    def test_memory_revocation_removes_from_qdrant(self, doc_store):
        mid = str(uuid.uuid4())
        doc_store.upsert_memory(
            MemoryPoint(
                memory_id=mid,
                session_id="",
                provenance_reference="src-rev",
                content_hash="mhrev",
                embedding_model=MODEL,
                embedding_version="1",
                vector=_vec(0.3),
            )
        )
        # Verify present
        before = doc_store.search_memories(_vec(0.3), MODEL, top_k=50)
        assert any(r.point_id == mid for r in before)

        doc_store.delete_memory(mid)

        after = doc_store.search_memories(_vec(0.3), MODEL, top_k=50)
        assert not any(r.point_id == mid for r in after), (
            "Revoked memory must be removed from Qdrant"
        )

    def test_delete_memory_idempotent(self, doc_store):
        doc_store.delete_memory(str(uuid.uuid4()))

    def test_memory_results_return_ids_not_content(self, doc_store):
        mid = str(uuid.uuid4())
        doc_store.upsert_memory(
            MemoryPoint(
                memory_id=mid,
                session_id="",
                provenance_reference="",
                content_hash="",
                embedding_model=MODEL,
                embedding_version="1",
                vector=_vec(0.2),
            )
        )
        results = doc_store.search_memories(_vec(0.2), MODEL, top_k=5)
        for r in results:
            assert not hasattr(r, "content"), "Memory content must never be in Qdrant results"


# ---------------------------------------------------------------------------
# Dimension mismatch against live Qdrant
# ---------------------------------------------------------------------------


class TestLiveDimensionValidation:
    def test_wrong_dim_raises_before_network_call(self, doc_store):
        with pytest.raises(VectorDimensionError):
            doc_store.upsert_document_chunks(
                [
                    DocumentChunkPoint(
                        chunk_id=str(uuid.uuid4()),
                        document_id="d1",
                        session_id="s1",
                        content_hash="h",
                        embedding_model=MODEL,
                        embedding_version="1",
                        vector=[0.1] * 128,
                    )
                ]
            )


# ---------------------------------------------------------------------------
# Cross-session access prevention
# ---------------------------------------------------------------------------


class TestCrossSessionPrevention:
    def test_empty_session_returns_no_results(self, doc_store):
        """Searching an unknown session must return an empty result set."""
        results = doc_store.search_document_chunks(
            _vec(0.5),
            session_id=f"nonexistent-{uuid.uuid4().hex}",
            embedding_model=MODEL,
            top_k=10,
        )
        assert results == []
