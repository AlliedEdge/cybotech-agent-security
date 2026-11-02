"""
VectorStore port — the application-facing abstraction for all vector operations.

Rules:
- Application services (DocumentService, RetrievalService, MemoryGuard) import
  only this module, never qdrant_client directly.
- Qdrant-specific types never leak past the adapter boundary.
- All search results are plain Python dataclasses so consumers stay decoupled.
- Point IDs are deterministic UUIDs derived from the canonical PostgreSQL record
  UUID, so upserts are idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class VectorStoreError(RuntimeError):
    """Base class for all vector-store errors."""


class VectorDimensionError(VectorStoreError):
    """Raised when a vector has the wrong number of dimensions."""


class VectorModelMismatchError(VectorStoreError):
    """Raised when the embedding model name does not match the collection config."""


class VectorStoreUnavailableError(VectorStoreError):
    """Raised when the vector store is not reachable."""


# ---------------------------------------------------------------------------
# Request / payload contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocumentChunkPoint:
    """Minimal payload stored in Qdrant alongside a document chunk vector.

    Sensitive text (chunk content) is NOT stored here — it lives in PostgreSQL.
    """

    chunk_id: str  # UUID string — the canonical DocumentChunk.id
    document_id: str  # UUID string — parent DocumentRecord.id
    session_id: str  # session scope for filtered search
    content_hash: str  # SHA-256 of the chunk text — integrity cross-check
    embedding_model: str  # model name, e.g. "text-embedding-3-small"
    embedding_version: str  # e.g. "1" or a semantic version tag
    vector: list[float]  # must be exactly `dimensions` elements


@dataclass(frozen=True)
class MemoryPoint:
    """Minimal payload stored in Qdrant alongside a memory vector.

    Sensitive content is NOT stored here — it lives in PostgreSQL.
    """

    memory_id: str  # UUID string — the canonical Memory.id
    session_id: str  # session scope (used for filtering in future)
    provenance_reference: str  # e.g. source_document_id or provenance key
    content_hash: str  # SHA-256 of memory content — integrity cross-check
    embedding_model: str  # model name
    embedding_version: str  # e.g. "1"
    vector: list[float]  # must be exactly `dimensions` elements


# ---------------------------------------------------------------------------
# Result contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchResult:
    """A single ANN hit returned by Qdrant, hydrated with its point ID and score.

    The canonical record must be re-fetched from PostgreSQL using `point_id`.
    Score is in [0.0, 1.0] — 1.0 means identical vector (cosine similarity).
    """

    point_id: str  # The chunk_id or memory_id stored in the payload
    score: float  # Cosine similarity score


# ---------------------------------------------------------------------------
# VectorStore Protocol (Port)
# ---------------------------------------------------------------------------


@runtime_checkable
class VectorStore(Protocol):
    """Abstract port for all vector storage and search operations.

    Implementations must be idempotent on upsert: calling upsert twice with the
    same point ID and identical vector must succeed without creating duplicates.

    Implementations must raise VectorDimensionError (not crash silently) if the
    supplied vector length does not match the collection dimension.

    Implementations must raise VectorModelMismatchError if the embedding_model
    in the payload does not match the collection's configured model name.
    """

    # ── Collection lifecycle ────────────────────────────────────────────────

    def ensure_collections(self) -> None:
        """Create collections if they do not already exist.

        Safe to call on every startup — must be idempotent.
        Does NOT recreate or wipe existing collections.
        """
        ...

    def health_check(self) -> bool:
        """Return True if the vector store is reachable and healthy."""
        ...

    # ── Document chunk operations ───────────────────────────────────────────

    def upsert_document_chunks(self, points: list[DocumentChunkPoint]) -> None:
        """Upsert one or more document chunk vectors.

        Idempotent: re-upserting the same chunk_id overwrites the old vector.
        Raises VectorDimensionError if any vector has the wrong length.
        Raises VectorModelMismatchError if embedding_model is inconsistent.
        """
        ...

    def search_document_chunks(
        self,
        query_vector: list[float],
        session_id: str,
        embedding_model: str,
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> list[SearchResult]:
        """Search document chunks scoped to a specific session.

        Filters:
          - session_id must match exactly (payload filter)
          - embedding_model must match exactly (payload filter)
          - soft-deleted chunks are excluded via caller-supplied session scope

        Returns at most top_k results, sorted by descending similarity score.
        Raises VectorDimensionError if query_vector has the wrong length.
        """
        ...

    def delete_document_chunks(self, chunk_ids: list[str]) -> None:
        """Delete document chunk vectors by their chunk_id.

        Idempotent: deleting a non-existent ID succeeds silently.
        """
        ...

    # ── Memory operations ───────────────────────────────────────────────────

    def upsert_memory(self, point: MemoryPoint) -> None:
        """Upsert a single memory vector.

        Idempotent: re-upserting the same memory_id overwrites the old vector.
        Raises VectorDimensionError if the vector has the wrong length.
        Raises VectorModelMismatchError if embedding_model is inconsistent.
        """
        ...

    def search_memories(
        self,
        query_vector: list[float],
        embedding_model: str,
        top_k: int = 20,
        min_score: float = 0.0,
    ) -> list[SearchResult]:
        """Search memory vectors (not session-scoped — trust filtering is PostgreSQL's job).

        Filters:
          - embedding_model must match exactly (payload filter)

        Returns at most top_k results, sorted by descending similarity score.
        The caller MUST re-fetch and re-validate each returned memory_id from
        PostgreSQL using is_eligible() before using the result.
        """
        ...

    def delete_memory(self, memory_id: str) -> None:
        """Delete a memory vector by its memory_id.

        Idempotent: deleting a non-existent ID succeeds silently.
        """
        ...
