"""
Unit tests for the Qdrant adapter and vector-store abstraction.

All tests use a mock QdrantClient so no real Qdrant instance is needed.
Tests cover:
- Collection configuration
- Vector dimension validation (dimension mismatch raises VectorDimensionError)
- Embedding model validation (model mismatch raises VectorModelMismatchError)
- Deterministic UUID5 point IDs (stable, idempotent)
- Idempotent upserts (second call with same ID does not raise)
- Similarity result mapping (chunk_id / memory_id extracted from payload)
- Metadata filter construction (session_id + embedding_model)
- Batch upsert splitting
- Deletion (chunk and memory)
- PostgreSQL hydration pattern (search returns IDs, not content)
- Error wrapping into VectorStoreError
- health_check returns True on success, False on failure
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from cybotech.infrastructure.vector_store.collections import CollectionConfig
from cybotech.infrastructure.vector_store.interface import (
    DocumentChunkPoint,
    MemoryPoint,
    SearchResult,
    VectorDimensionError,
    VectorModelMismatchError,
    VectorStoreError,
)
from cybotech.infrastructure.vector_store.qdrant_adapter import (
    NAMESPACE_DOCUMENT_CHUNK,
    NAMESPACE_MEMORY,
    QdrantVectorStore,
    _point_id_for_chunk,
    _point_id_for_memory,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DOC_MODEL = "text-embedding-3-small"
MEM_MODEL = "text-embedding-3-small"
DIM = 384


def _doc_cfg(name: str = "test_doc_chunks") -> CollectionConfig:
    return CollectionConfig(
        name=name,
        dimension=DIM,
        embedding_model=DOC_MODEL,
        distance="Cosine",
        indexed_payload_fields=("session_id", "embedding_model"),
    )


def _mem_cfg(name: str = "test_memory_vectors") -> CollectionConfig:
    return CollectionConfig(
        name=name,
        dimension=DIM,
        embedding_model=MEM_MODEL,
        distance="Cosine",
        indexed_payload_fields=("embedding_model",),
    )


def _make_store(client: MagicMock | None = None) -> QdrantVectorStore:
    if client is None:
        client = MagicMock()
        # Simulate both collections already exist so ensure_collections is a no-op.
        col_mock = MagicMock()
        col_mock.name = "test_doc_chunks"
        col_mock2 = MagicMock()
        col_mock2.name = "test_memory_vectors"
        collections_result = MagicMock()
        collections_result.collections = [col_mock, col_mock2]
        client.get_collections.return_value = collections_result
    return QdrantVectorStore(
        client=client,
        document_config=_doc_cfg(),
        memory_config=_mem_cfg(),
    )


def _vec(dim: int = DIM, value: float = 0.1) -> list[float]:
    return [value] * dim


def _chunk_point(chunk_id: str = "chunk-1", session_id: str = "s1") -> DocumentChunkPoint:
    return DocumentChunkPoint(
        chunk_id=chunk_id,
        document_id="doc-1",
        session_id=session_id,
        content_hash="abc123",
        embedding_model=DOC_MODEL,
        embedding_version="1",
        vector=_vec(),
    )


def _memory_point(memory_id: str = "mem-1") -> MemoryPoint:
    return MemoryPoint(
        memory_id=memory_id,
        session_id="",
        provenance_reference="src-1",
        content_hash="def456",
        embedding_model=MEM_MODEL,
        embedding_version="1",
        vector=_vec(),
    )


# ---------------------------------------------------------------------------
# Deterministic point ID tests
# ---------------------------------------------------------------------------


class TestPointIds:
    def test_chunk_id_is_deterministic(self):
        assert _point_id_for_chunk("abc") == _point_id_for_chunk("abc")

    def test_memory_id_is_deterministic(self):
        assert _point_id_for_memory("abc") == _point_id_for_memory("abc")

    def test_chunk_and_memory_ids_differ(self):
        assert _point_id_for_chunk("abc") != _point_id_for_memory("abc")

    def test_chunk_id_uses_correct_namespace(self):
        expected = str(uuid.uuid5(NAMESPACE_DOCUMENT_CHUNK, "xyz"))
        assert _point_id_for_chunk("xyz") == expected

    def test_memory_id_uses_correct_namespace(self):
        expected = str(uuid.uuid5(NAMESPACE_MEMORY, "xyz"))
        assert _point_id_for_memory("xyz") == expected

    def test_different_inputs_produce_different_ids(self):
        assert _point_id_for_chunk("a") != _point_id_for_chunk("b")


# ---------------------------------------------------------------------------
# Dimension validation
# ---------------------------------------------------------------------------


class TestDimensionValidation:
    def test_upsert_chunk_wrong_dim_raises(self):
        store = _make_store()
        bad = _chunk_point()
        bad = DocumentChunkPoint(
            chunk_id=bad.chunk_id,
            document_id=bad.document_id,
            session_id=bad.session_id,
            content_hash=bad.content_hash,
            embedding_model=bad.embedding_model,
            embedding_version=bad.embedding_version,
            vector=[0.1] * 128,  # wrong dim
        )
        with pytest.raises(VectorDimensionError):
            store.upsert_document_chunks([bad])

    def test_upsert_memory_wrong_dim_raises(self):
        store = _make_store()
        bad = MemoryPoint(
            memory_id="m1",
            session_id="",
            provenance_reference="",
            content_hash="",
            embedding_model=MEM_MODEL,
            embedding_version="1",
            vector=[0.1] * 512,
        )
        with pytest.raises(VectorDimensionError):
            store.upsert_memory(bad)

    def test_search_chunks_wrong_dim_raises(self):
        store = _make_store()
        with pytest.raises(VectorDimensionError):
            store.search_document_chunks([0.1] * 128, "s1", DOC_MODEL)

    def test_search_memories_wrong_dim_raises(self):
        store = _make_store()
        with pytest.raises(VectorDimensionError):
            store.search_memories([0.1] * 64, MEM_MODEL)


# ---------------------------------------------------------------------------
# Model validation
# ---------------------------------------------------------------------------


class TestModelValidation:
    def test_upsert_chunk_wrong_model_raises(self):
        store = _make_store()
        bad = DocumentChunkPoint(
            chunk_id="c1",
            document_id="d1",
            session_id="s1",
            content_hash="h",
            embedding_model="wrong-model",
            embedding_version="1",
            vector=_vec(),
        )
        with pytest.raises(VectorModelMismatchError):
            store.upsert_document_chunks([bad])

    def test_upsert_memory_wrong_model_raises(self):
        store = _make_store()
        bad = MemoryPoint(
            memory_id="m1",
            session_id="",
            provenance_reference="",
            content_hash="",
            embedding_model="wrong-model",
            embedding_version="1",
            vector=_vec(),
        )
        with pytest.raises(VectorModelMismatchError):
            store.upsert_memory(bad)

    def test_search_chunks_wrong_model_raises(self):
        store = _make_store()
        with pytest.raises(VectorModelMismatchError):
            store.search_document_chunks(_vec(), "s1", "wrong-model")

    def test_search_memories_wrong_model_raises(self):
        store = _make_store()
        with pytest.raises(VectorModelMismatchError):
            store.search_memories(_vec(), "wrong-model")


# ---------------------------------------------------------------------------
# Upsert behaviour
# ---------------------------------------------------------------------------


class TestUpsertDocumentChunks:
    def test_upsert_calls_client(self):
        client = MagicMock()
        store = _make_store(client)
        store.upsert_document_chunks([_chunk_point()])
        client.upsert.assert_called_once()
        kwargs = client.upsert.call_args.kwargs
        assert kwargs["collection_name"] == "test_doc_chunks"
        assert kwargs["wait"] is True

    def test_upsert_uses_deterministic_point_id(self):
        client = MagicMock()
        store = _make_store(client)
        chunk_id = "my-chunk-uuid"
        store.upsert_document_chunks([_chunk_point(chunk_id=chunk_id)])
        points = client.upsert.call_args.kwargs["points"]
        assert points[0].id == _point_id_for_chunk(chunk_id)

    def test_upsert_payload_contains_no_content(self):
        """Chunk content must NOT be stored in Qdrant — it lives in PostgreSQL."""
        client = MagicMock()
        store = _make_store(client)
        store.upsert_document_chunks([_chunk_point()])
        points = client.upsert.call_args.kwargs["points"]
        payload = points[0].payload
        assert "content" not in payload
        assert "chunk_id" in payload
        assert "session_id" in payload

    def test_upsert_empty_list_skips_client(self):
        client = MagicMock()
        store = _make_store(client)
        store.upsert_document_chunks([])
        client.upsert.assert_not_called()

    def test_upsert_idempotent_second_call_does_not_raise(self):
        client = MagicMock()
        store = _make_store(client)
        pt = _chunk_point()
        store.upsert_document_chunks([pt])
        store.upsert_document_chunks([pt])  # same point — must not raise
        assert client.upsert.call_count == 2

    def test_upsert_wraps_client_error(self):
        client = MagicMock()
        client.upsert.side_effect = RuntimeError("Qdrant unavailable")
        store = _make_store(client)
        with pytest.raises(VectorStoreError):
            store.upsert_document_chunks([_chunk_point()])


class TestUpsertMemory:
    def test_upsert_memory_calls_client(self):
        client = MagicMock()
        store = _make_store(client)
        store.upsert_memory(_memory_point())
        client.upsert.assert_called_once()

    def test_upsert_memory_uses_deterministic_id(self):
        client = MagicMock()
        store = _make_store(client)
        mem_id = "my-memory-uuid"
        store.upsert_memory(_memory_point(memory_id=mem_id))
        points = client.upsert.call_args.kwargs["points"]
        assert points[0].id == _point_id_for_memory(mem_id)

    def test_upsert_memory_payload_has_no_content(self):
        """Memory content must NOT be stored in Qdrant."""
        client = MagicMock()
        store = _make_store(client)
        store.upsert_memory(_memory_point())
        payload = client.upsert.call_args.kwargs["points"][0].payload
        assert "content" not in payload
        assert "memory_id" in payload


# ---------------------------------------------------------------------------
# Search result mapping
# ---------------------------------------------------------------------------


class TestSearchDocumentChunks:
    def _mock_hit(self, chunk_id: str, score: float):
        hit = MagicMock()
        hit.payload = {
            "chunk_id": chunk_id,
            "session_id": "s1",
            "embedding_model": DOC_MODEL,
        }
        hit.score = score
        return hit

    def _mock_response(self, hits):
        """Wrap hits in a QueryResponse-like object with a .points attribute."""
        resp = MagicMock()
        resp.points = hits
        return resp

    def test_search_returns_search_results(self):
        client = MagicMock()
        client.query_points.return_value = self._mock_response(
            [
                self._mock_hit("chunk-abc", 0.95),
                self._mock_hit("chunk-def", 0.80),
            ]
        )
        store = _make_store(client)
        results = store.search_document_chunks(_vec(), "s1", DOC_MODEL, top_k=5)
        assert len(results) == 2
        assert isinstance(results[0], SearchResult)
        assert results[0].point_id == "chunk-abc"
        assert results[0].score == pytest.approx(0.95)

    def test_search_passes_session_filter(self):
        client = MagicMock()
        client.query_points.return_value = self._mock_response([])
        store = _make_store(client)
        store.search_document_chunks(_vec(), "session-xyz", DOC_MODEL)
        call_kwargs = client.query_points.call_args.kwargs
        filter_obj = call_kwargs["query_filter"]
        # Check session_id appears in the filter conditions
        must_conditions = filter_obj.must
        keys = [c.key for c in must_conditions]
        assert "session_id" in keys
        assert "embedding_model" in keys

    def test_search_skips_hits_without_chunk_id(self):
        client = MagicMock()
        bad_hit = MagicMock()
        bad_hit.payload = {}  # missing chunk_id
        bad_hit.score = 0.9
        client.query_points.return_value = self._mock_response([bad_hit])
        store = _make_store(client)
        results = store.search_document_chunks(_vec(), "s1", DOC_MODEL)
        assert results == []

    def test_search_returns_ids_not_content(self):
        """Callers must hydrate from PostgreSQL — Qdrant must not return content."""
        client = MagicMock()
        client.query_points.return_value = self._mock_response([self._mock_hit("chunk-1", 0.9)])
        store = _make_store(client)
        results = store.search_document_chunks(_vec(), "s1", DOC_MODEL)
        result = results[0]
        assert not hasattr(result, "content")
        assert result.point_id == "chunk-1"


class TestSearchMemories:
    def _mock_hit(self, memory_id: str, score: float):
        hit = MagicMock()
        hit.payload = {"memory_id": memory_id, "embedding_model": MEM_MODEL}
        hit.score = score
        return hit

    def _mock_response(self, hits):
        resp = MagicMock()
        resp.points = hits
        return resp

    def test_search_memories_returns_results(self):
        client = MagicMock()
        client.query_points.return_value = self._mock_response([self._mock_hit("mem-1", 0.88)])
        store = _make_store(client)
        results = store.search_memories(_vec(), MEM_MODEL)
        assert len(results) == 1
        assert results[0].point_id == "mem-1"
        assert results[0].score == pytest.approx(0.88)

    def test_search_memories_passes_model_filter(self):
        client = MagicMock()
        client.query_points.return_value = self._mock_response([])
        store = _make_store(client)
        store.search_memories(_vec(), MEM_MODEL)
        call_kwargs = client.query_points.call_args.kwargs
        conditions = call_kwargs["query_filter"].must
        keys = [c.key for c in conditions]
        assert "embedding_model" in keys
        # session_id must NOT be a filter — memory has no session scope
        assert "session_id" not in keys

    def test_search_memories_no_content_returned(self):
        """Qdrant memory results must not carry memory content."""
        client = MagicMock()
        client.query_points.return_value = self._mock_response([self._mock_hit("mem-42", 0.77)])
        store = _make_store(client)
        results = store.search_memories(_vec(), MEM_MODEL)
        assert not hasattr(results[0], "content")


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------


class TestDeletion:
    def test_delete_chunks_calls_client(self):
        client = MagicMock()
        store = _make_store(client)
        store.delete_document_chunks(["chunk-1", "chunk-2"])
        client.delete.assert_called_once()
        kwargs = client.delete.call_args.kwargs
        assert kwargs["collection_name"] == "test_doc_chunks"
        assert kwargs["wait"] is True

    def test_delete_chunks_uses_deterministic_ids(self):
        client = MagicMock()
        store = _make_store(client)
        store.delete_document_chunks(["chunk-abc"])
        selector = client.delete.call_args.kwargs["points_selector"]
        assert _point_id_for_chunk("chunk-abc") in selector.points

    def test_delete_chunks_empty_list_no_call(self):
        client = MagicMock()
        store = _make_store(client)
        store.delete_document_chunks([])
        client.delete.assert_not_called()

    def test_delete_memory_calls_client(self):
        client = MagicMock()
        store = _make_store(client)
        store.delete_memory("mem-1")
        client.delete.assert_called_once()

    def test_delete_memory_uses_deterministic_id(self):
        client = MagicMock()
        store = _make_store(client)
        store.delete_memory("mem-xyz")
        selector = client.delete.call_args.kwargs["points_selector"]
        assert _point_id_for_memory("mem-xyz") in selector.points

    def test_delete_wraps_client_error(self):
        client = MagicMock()
        client.delete.side_effect = RuntimeError("network error")
        store = _make_store(client)
        with pytest.raises(VectorStoreError):
            store.delete_document_chunks(["chunk-1"])


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    def test_health_check_returns_true_on_success(self):
        client = MagicMock()
        store = _make_store(client)
        assert store.health_check() is True

    def test_health_check_returns_false_on_failure(self):
        client = MagicMock()
        client.get_collections.side_effect = ConnectionError("Qdrant down")
        store = _make_store(client)
        assert store.health_check() is False


# ---------------------------------------------------------------------------
# ensure_collections
# ---------------------------------------------------------------------------


class TestEnsureCollections:
    def test_create_collection_when_absent(self):
        client = MagicMock()
        # Simulate empty Qdrant
        empty = MagicMock()
        empty.collections = []
        client.get_collections.return_value = empty
        client.create_payload_index.return_value = None
        store = _make_store(client)
        store.ensure_collections()
        assert client.create_collection.call_count == 2

    def test_no_create_when_collections_exist(self):
        client = MagicMock()
        # Simulate both collections already present
        c1, c2 = MagicMock(), MagicMock()
        c1.name, c2.name = "test_doc_chunks", "test_memory_vectors"
        result = MagicMock()
        result.collections = [c1, c2]
        client.get_collections.return_value = result
        client.create_payload_index.return_value = None
        store = _make_store(client)
        store.ensure_collections()
        client.create_collection.assert_not_called()
