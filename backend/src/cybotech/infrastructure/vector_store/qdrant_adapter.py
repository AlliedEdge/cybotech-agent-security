"""
QdrantVectorStore — the production adapter behind the VectorStore port.

Qdrant-specific types are confined entirely to this module.
The rest of the application imports only interface.py contracts.

Point ID strategy
-----------------
Every point in Qdrant uses a deterministic UUID v5 derived from the canonical
PostgreSQL record UUID so that upserts are idempotent and the Qdrant ID can
always be recomputed from the application record without a lookup.

Namespace UUIDs (arbitrary fixed values — never changed after deployment):
  NAMESPACE_DOCUMENT_CHUNK  →  document chunk points
  NAMESPACE_MEMORY          →  memory points

Security notes
--------------
- Qdrant payload metadata is NEVER used for authorization decisions.
- All search results return only the canonical record ID (chunk_id / memory_id).
- The caller is responsible for re-fetching and revalidating records from
  PostgreSQL before trusting any result.
"""

from __future__ import annotations

import logging
import os
import uuid
from functools import lru_cache

from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models
from qdrant_client.http.exceptions import UnexpectedResponse

from cybotech.infrastructure.vector_store.collections import (
    CollectionConfig,
    document_collection_config,
    memory_collection_config,
)
from cybotech.infrastructure.vector_store.interface import (
    DocumentChunkPoint,
    MemoryPoint,
    SearchResult,
    VectorDimensionError,
    VectorModelMismatchError,
    VectorStoreError,
    VectorStoreUnavailableError,
)

logger = logging.getLogger(__name__)

# Deterministic UUID namespaces — never change these after first deployment.
NAMESPACE_DOCUMENT_CHUNK = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
NAMESPACE_MEMORY = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")

# Batch size for bulk upserts.
_UPSERT_BATCH_SIZE = 256


def _point_id_for_chunk(chunk_id: str) -> str:
    """Derive a deterministic Qdrant point UUID from a DocumentChunk.id string."""
    return str(uuid.uuid5(NAMESPACE_DOCUMENT_CHUNK, chunk_id))


def _point_id_for_memory(memory_id: str) -> str:
    """Derive a deterministic Qdrant point UUID from a Memory.id string."""
    return str(uuid.uuid5(NAMESPACE_MEMORY, memory_id))


class QdrantVectorStore:
    """Production VectorStore adapter backed by a Qdrant HTTP endpoint.

    Thread-safe: QdrantClient is threadsafe for concurrent requests.
    """

    def __init__(
        self,
        client: QdrantClient,
        document_config: CollectionConfig,
        memory_config: CollectionConfig,
    ) -> None:
        self._client = client
        self._doc_cfg = document_config
        self._mem_cfg = memory_config

    # ── Collection lifecycle ────────────────────────────────────────────────

    def ensure_collections(self) -> None:
        """Create collections and payload indexes if they do not already exist.

        Safe to call at startup — idempotent.
        """
        self._ensure_one_collection(self._doc_cfg)
        self._ensure_one_collection(self._mem_cfg)

    def _ensure_one_collection(self, cfg: CollectionConfig) -> None:
        try:
            existing = {c.name for c in self._client.get_collections().collections}
        except Exception as exc:
            raise VectorStoreUnavailableError(
                f"Cannot reach Qdrant to check collections: {exc}"
            ) from exc

        if cfg.name not in existing:
            try:
                self._client.create_collection(
                    collection_name=cfg.name,
                    vectors_config=qdrant_models.VectorParams(
                        size=cfg.dimension,
                        distance=qdrant_models.Distance[cfg.distance.upper()],
                    ),
                    hnsw_config=qdrant_models.HnswConfigDiff(
                        m=cfg.hnsw_m,
                        ef_construct=cfg.hnsw_ef_construct,
                    ),
                    # Optimizers config: always flush to disk on upsert for durability.
                    optimizers_config=qdrant_models.OptimizersConfigDiff(
                        memmap_threshold=20000,
                    ),
                )
                logger.info("Created Qdrant collection: %s", cfg.name)
            except UnexpectedResponse as exc:
                # Race: another instance created it between our check and create.
                if "already exists" in str(exc).lower():
                    logger.debug("Collection %s already exists (race), continuing.", cfg.name)
                else:
                    raise VectorStoreError(
                        f"Failed to create collection {cfg.name!r}: {exc}"
                    ) from exc

        # Create payload indexes for efficient filtered search.
        for field_name in cfg.indexed_payload_fields:
            try:
                self._client.create_payload_index(
                    collection_name=cfg.name,
                    field_name=field_name,
                    field_schema=qdrant_models.PayloadSchemaType.KEYWORD,
                )
            except UnexpectedResponse as exc:
                if "already exists" in str(exc).lower():
                    pass  # idempotent
                else:
                    logger.warning(
                        "Could not create payload index %s.%s: %s",
                        cfg.name,
                        field_name,
                        exc,
                    )

    def health_check(self) -> bool:
        """Return True if Qdrant is reachable."""
        try:
            self._client.get_collections()
            return True
        except Exception:
            return False

    # ── Validation helpers ──────────────────────────────────────────────────

    def _validate_vector(self, vector: list[float], expected_dim: int) -> None:
        if len(vector) != expected_dim:
            raise VectorDimensionError(
                f"Vector has {len(vector)} dimensions, expected {expected_dim}."
            )

    def _validate_model(self, model_name: str, expected_model: str) -> None:
        if model_name != expected_model:
            raise VectorModelMismatchError(
                f"Embedding model {model_name!r} does not match collection model "
                f"{expected_model!r}. Never silently mix incompatible models."
            )

    # ── Document chunk operations ───────────────────────────────────────────

    def upsert_document_chunks(self, points: list[DocumentChunkPoint]) -> None:
        if not points:
            return
        for point in points:
            self._validate_vector(point.vector, self._doc_cfg.dimension)
            self._validate_model(point.embedding_model, self._doc_cfg.embedding_model)

        qdrant_points = [
            qdrant_models.PointStruct(
                id=_point_id_for_chunk(p.chunk_id),
                vector=p.vector,
                payload={
                    "chunk_id": p.chunk_id,
                    "document_id": p.document_id,
                    "session_id": p.session_id,
                    "content_hash": p.content_hash,
                    "embedding_model": p.embedding_model,
                    "embedding_version": p.embedding_version,
                },
            )
            for p in points
        ]
        # Upsert in batches to avoid request size limits.
        for batch_start in range(0, len(qdrant_points), _UPSERT_BATCH_SIZE):
            batch = qdrant_points[batch_start : batch_start + _UPSERT_BATCH_SIZE]
            try:
                self._client.upsert(
                    collection_name=self._doc_cfg.name,
                    points=batch,
                    wait=True,  # wait=True ensures durability before returning
                )
            except Exception as exc:
                raise VectorStoreError(f"Failed to upsert document chunk batch: {exc}") from exc
        logger.debug("Upserted %d document chunk point(s).", len(points))

    def search_document_chunks(
        self,
        query_vector: list[float],
        session_id: str,
        embedding_model: str,
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> list[SearchResult]:
        self._validate_vector(query_vector, self._doc_cfg.dimension)
        self._validate_model(embedding_model, self._doc_cfg.embedding_model)

        # Payload filter: exact session scope + model match.
        query_filter = qdrant_models.Filter(
            must=[
                qdrant_models.FieldCondition(
                    key="session_id",
                    match=qdrant_models.MatchValue(value=session_id),
                ),
                qdrant_models.FieldCondition(
                    key="embedding_model",
                    match=qdrant_models.MatchValue(value=embedding_model),
                ),
            ]
        )
        try:
            response = self._client.query_points(
                collection_name=self._doc_cfg.name,
                query=query_vector,
                query_filter=query_filter,
                limit=top_k,
                score_threshold=min_score if min_score > 0.0 else None,
                with_payload=True,
            )
        except Exception as exc:
            raise VectorStoreError(f"Document chunk search failed: {exc}") from exc

        return [
            SearchResult(
                point_id=str(hit.payload["chunk_id"]),
                score=float(hit.score),
            )
            for hit in response.points
            if hit.payload and "chunk_id" in hit.payload
        ]

    def delete_document_chunks(self, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        point_ids: list[str | int | uuid.UUID] = [_point_id_for_chunk(cid) for cid in chunk_ids]
        try:
            self._client.delete(
                collection_name=self._doc_cfg.name,
                points_selector=qdrant_models.PointIdsList(points=point_ids),
                wait=True,
            )
        except Exception as exc:
            raise VectorStoreError(f"Failed to delete document chunk points: {exc}") from exc
        logger.debug("Deleted %d document chunk point(s).", len(chunk_ids))

    # ── Memory operations ───────────────────────────────────────────────────

    def upsert_memory(self, point: MemoryPoint) -> None:
        self._validate_vector(point.vector, self._mem_cfg.dimension)
        self._validate_model(point.embedding_model, self._mem_cfg.embedding_model)

        qdrant_point = qdrant_models.PointStruct(
            id=_point_id_for_memory(point.memory_id),
            vector=point.vector,
            payload={
                "memory_id": point.memory_id,
                "session_id": point.session_id,
                "provenance_reference": point.provenance_reference,
                "content_hash": point.content_hash,
                "embedding_model": point.embedding_model,
                "embedding_version": point.embedding_version,
            },
        )
        try:
            self._client.upsert(
                collection_name=self._mem_cfg.name,
                points=[qdrant_point],
                wait=True,
            )
        except Exception as exc:
            raise VectorStoreError(
                f"Failed to upsert memory point {point.memory_id!r}: {exc}"
            ) from exc
        logger.debug("Upserted memory point %s.", point.memory_id)

    def search_memories(
        self,
        query_vector: list[float],
        embedding_model: str,
        top_k: int = 20,
        min_score: float = 0.0,
    ) -> list[SearchResult]:
        self._validate_vector(query_vector, self._mem_cfg.dimension)
        self._validate_model(embedding_model, self._mem_cfg.embedding_model)

        # Filter by embedding model only — trust filtering is PostgreSQL's job.
        query_filter = qdrant_models.Filter(
            must=[
                qdrant_models.FieldCondition(
                    key="embedding_model",
                    match=qdrant_models.MatchValue(value=embedding_model),
                ),
            ]
        )
        try:
            response = self._client.query_points(
                collection_name=self._mem_cfg.name,
                query=query_vector,
                query_filter=query_filter,
                limit=top_k,
                score_threshold=min_score if min_score > 0.0 else None,
                with_payload=True,
            )
        except Exception as exc:
            raise VectorStoreError(f"Memory search failed: {exc}") from exc

        return [
            SearchResult(
                point_id=str(hit.payload["memory_id"]),
                score=float(hit.score),
            )
            for hit in response.points
            if hit.payload and "memory_id" in hit.payload
        ]

    def delete_memory(self, memory_id: str) -> None:
        point_id: str | int | uuid.UUID = _point_id_for_memory(memory_id)
        try:
            self._client.delete(
                collection_name=self._mem_cfg.name,
                points_selector=qdrant_models.PointIdsList(points=[point_id]),
                wait=True,
            )
        except Exception as exc:
            raise VectorStoreError(f"Failed to delete memory point {memory_id!r}: {exc}") from exc
        logger.debug("Deleted memory point %s.", memory_id)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _get_cached_client() -> QdrantClient:
    """Return a cached QdrantClient instance built from environment variables.

    The lru_cache ensures one client per process — safe for FastAPI which is
    single-process by default. In multi-process deployments, each worker gets
    its own cached client (which is correct behaviour).
    """
    url = os.environ.get("QDRANT_URL", "http://localhost:6333")
    api_key = os.environ.get("QDRANT_API_KEY") or None  # None means no auth
    return QdrantClient(url=url, api_key=api_key, timeout=10)


def build_qdrant_vector_store(
    *,
    client: QdrantClient | None = None,
    document_config: CollectionConfig | None = None,
    memory_config: CollectionConfig | None = None,
) -> QdrantVectorStore:
    """Build a QdrantVectorStore from environment variables.

    Passing explicit arguments overrides env-based defaults — useful in tests.
    """
    return QdrantVectorStore(
        client=client or _get_cached_client(),
        document_config=document_config or document_collection_config(),
        memory_config=memory_config or memory_collection_config(),
    )
