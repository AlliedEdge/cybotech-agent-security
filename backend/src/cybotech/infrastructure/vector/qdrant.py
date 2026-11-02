"""
Qdrant vector store — dedicated vector DB for memory and document chunk RAG.

Two named collections:
  - cybotech_memories      : one point per trusted Memory (keyed by memory UUID)
  - cybotech_documents     : one point per DocumentChunk  (keyed by chunk UUID)

Both collections use cosine distance and 384-dimensional vectors, matching
the LocalEmbedder (offline) and OpenAI text-embedding-3-small (live) output.

QdrantStore is the single injection point: callers receive it via
runtime_qdrant_store() in api/v1/security.py and pass it into
RetrievalService and DocumentService.  The store is stateless beyond
the Qdrant client reference, so it is safe to share per-request.

Offline / local-dev mode:
  Pass location=":memory:" to QdrantStore to get an embedded in-process
  Qdrant instance (no Docker, no network).  This is what local.py does.
"""

from __future__ import annotations

from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

DIMENSIONS = 384
DISTANCE = qmodels.Distance.COSINE

# Collection names — centralised so callers never hard-code strings.
MEMORY_COLLECTION = "cybotech_memories"
DOCUMENT_COLLECTION = "cybotech_documents"


class QdrantStore:
    """
    Thin, focused wrapper around QdrantClient for this project.

    Responsibilities:
      - Ensure both collections exist (idempotent).
      - Upsert / delete points by string ID.
      - Search a collection and return (id, score, payload) triples.

    It intentionally knows nothing about Memory or DocumentChunk domain
    objects so it stays a pure infrastructure concern.
    """

    def __init__(self, client: QdrantClient) -> None:
        self.client = client
        self._ensure_collections()

    # ------------------------------------------------------------------
    # Collection bootstrap
    # ------------------------------------------------------------------

    def _ensure_collections(self) -> None:
        """Create collections if they don't exist yet (idempotent)."""
        existing = {c.name for c in self.client.get_collections().collections}
        for name in (MEMORY_COLLECTION, DOCUMENT_COLLECTION):
            if name not in existing:
                self.client.create_collection(
                    collection_name=name,
                    vectors_config=qmodels.VectorParams(
                        size=DIMENSIONS,
                        distance=DISTANCE,
                    ),
                )

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def upsert_memory(
        self,
        memory_id: str,
        vector: list[float],
        payload: dict[str, Any],
    ) -> None:
        """Store or update a memory vector.  payload holds filterable metadata."""
        self.client.upsert(
            collection_name=MEMORY_COLLECTION,
            points=[
                qmodels.PointStruct(
                    id=_qdrant_id(memory_id),
                    vector=vector,
                    payload={**payload, "_str_id": memory_id},
                )
            ],
        )

    def delete_memory(self, memory_id: str) -> None:
        """Remove a memory point (called on quarantine / revocation)."""
        self.client.delete(
            collection_name=MEMORY_COLLECTION,
            points_selector=qmodels.PointIdsList(points=[_qdrant_id(memory_id)]),
        )

    def upsert_document_chunk(
        self,
        chunk_id: str,
        vector: list[float],
        payload: dict[str, Any],
    ) -> None:
        """Store a document chunk vector with its metadata payload."""
        self.client.upsert(
            collection_name=DOCUMENT_COLLECTION,
            points=[
                qmodels.PointStruct(
                    id=_qdrant_id(chunk_id),
                    vector=vector,
                    payload={**payload, "_str_id": chunk_id},
                )
            ],
        )

    def delete_document_chunks(self, document_id: str) -> None:
        """Remove all chunk vectors belonging to a document (on document delete)."""
        self.client.delete(
            collection_name=DOCUMENT_COLLECTION,
            points_selector=qmodels.FilterSelector(
                filter=qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key="document_id",
                            match=qmodels.MatchValue(value=document_id),
                        )
                    ]
                )
            ),
        )

    # ------------------------------------------------------------------
    # Search operations
    # ------------------------------------------------------------------

    def search_memories(
        self,
        vector: list[float],
        top_k: int = 20,
        filter_payload: dict[str, Any] | None = None,
    ) -> list[tuple[str, float, dict[str, Any]]]:
        """
        ANN search over the memory collection.

        Returns a list of (memory_id_str, cosine_score, payload) sorted
        by descending score (most similar first).
        """
        qdrant_filter = _build_filter(filter_payload) if filter_payload else None
        results = self.client.query_points(
            collection_name=MEMORY_COLLECTION,
            query=vector,
            limit=top_k,
            query_filter=qdrant_filter,
            with_payload=True,
        )
        return [
            (hit.payload["_str_id"], hit.score, hit.payload)  # type: ignore[index]
            for hit in results.points
        ]

    def search_documents(
        self,
        vector: list[float],
        top_k: int = 5,
        filter_payload: dict[str, Any] | None = None,
    ) -> list[tuple[str, float, dict[str, Any]]]:
        """
        ANN search over the document chunk collection.

        Returns a list of (chunk_id_str, cosine_score, payload) sorted
        by descending score (most similar first).
        """
        qdrant_filter = _build_filter(filter_payload) if filter_payload else None
        results = self.client.query_points(
            collection_name=DOCUMENT_COLLECTION,
            query=vector,
            limit=top_k,
            query_filter=qdrant_filter,
            with_payload=True,
        )
        return [
            (hit.payload["_str_id"], hit.score, hit.payload)  # type: ignore[index]
            for hit in results.points
        ]

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def health(self) -> str:
        """Return 'healthy' if Qdrant is reachable, 'unhealthy' otherwise."""
        try:
            self.client.get_collections()
            return "healthy"
        except Exception:
            return "unhealthy"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _qdrant_id(str_id: str) -> str:
    """
    Qdrant point IDs must be either unsigned integers or UUID strings.
    We pass the raw UUID string directly (all our IDs are UUIDs).
    """
    return str_id


def _build_filter(payload: dict[str, Any]) -> qmodels.Filter:
    """Build a Qdrant must-match filter from a flat key→value dict."""
    conditions = [
        qmodels.FieldCondition(key=k, match=qmodels.MatchValue(value=v))
        for k, v in payload.items()
    ]
    return qmodels.Filter(must=conditions)


# ---------------------------------------------------------------------------
# Factory helpers (used by local.py and runtime_qdrant_store)
# ---------------------------------------------------------------------------


def make_in_memory_store() -> QdrantStore:
    """Create an embedded in-process Qdrant instance — no server required."""
    client = QdrantClient(location=":memory:")
    return QdrantStore(client)


def make_remote_store(url: str, api_key: str | None = None) -> QdrantStore:
    """Connect to a running Qdrant server (Docker Compose or cloud)."""
    client = QdrantClient(url=url, api_key=api_key or None)
    return QdrantStore(client)
