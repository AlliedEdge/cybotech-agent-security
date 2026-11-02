"""
Trusted-memory retrieval service.

Migration note (feat/qdrant-vector-db-migration)
------------------------------------------------
Vector similarity search now routes through the ``VectorStore`` abstraction.
PostgreSQL remains the sole authority on memory trust — the retrieval flow is:

  1. Query is embedded via the injected Embedder.
  2. Qdrant performs ANN search filtered by ``embedding_model``.
  3. Qdrant returns (memory_id, score) candidate pairs.
  4. PostgreSQL fetches each candidate Memory row by its UUID primary key.
  5. ``is_eligible()`` re-validates every candidate in Python against the live
     PostgreSQL state (status, expiry, revocation, provenance).
  6. Only fully-eligible memories are returned to the caller.

Critical invariant: a vector's presence in Qdrant never implies the associated
memory is trusted.  The existence check in step 4 and the ``is_eligible()``
check in step 5 are the canonical trust gate.

Fallback: when no VectorStore is injected (unit tests / SQLite), the original
dual-path implementation is preserved so existing tests continue to pass.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from openai import OpenAI
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from cybotech.agents.registry import Operation, require_permission
from cybotech.infrastructure.database.models import Memory, MemoryStatus

if TYPE_CHECKING:
    from cybotech.infrastructure.vector_store.interface import VectorStore

# Embedding version tag stored in Qdrant payload.
_EMBEDDING_VERSION = "1"


class Embedder(Protocol):
    model_name: str

    def embed(self, text: str) -> list[float]: ...


class OpenAIEmbedder:
    """Production embedding adapter using the configured OpenAI-compatible client."""

    def __init__(self, client: OpenAI, model_name: str = "text-embedding-3-small") -> None:
        self.client = client
        self.model_name = model_name

    def embed(self, text: str) -> list[float]:
        response = self.client.embeddings.create(model=self.model_name, input=text, dimensions=384)
        return list(response.data[0].embedding)


class RetrievalService:
    def __init__(
        self,
        db: Session,
        embedder: Embedder,
        dimensions: int = 384,
        vector_store: VectorStore | None = None,
    ) -> None:
        self.db = db
        self.embedder = embedder
        self.dimensions = dimensions
        # When None the service falls back to the original pgvector/Python path
        # so that existing SQLite-based unit tests pass unchanged.
        self._vector_store = vector_store

    def retrieve(
        self,
        query: str,
        *,
        max_results: int = 5,
        principal: str = "primary_assistant",
        min_similarity: float = 0.0,
    ) -> list[Memory]:
        require_permission(principal, Operation.RETRIEVE_MEMORY)
        if not 1 <= max_results <= 100 or not 0 <= min_similarity <= 1:
            raise ValueError("invalid retrieval bounds")
        if not query.strip():
            return []
        vector = self.embedder.embed(query)
        if len(vector) != self.dimensions:
            raise ValueError(f"embedding dimension must be {self.dimensions}")

        if self._vector_store is not None:
            return self._retrieve_via_qdrant(
                vector, max_results=max_results, min_similarity=min_similarity
            )
        return self._retrieve_legacy(vector, max_results=max_results, min_similarity=min_similarity)

    # ── Qdrant path ────────────────────────────────────────────────────────

    def _retrieve_via_qdrant(
        self,
        query_vector: list[float],
        *,
        max_results: int,
        min_similarity: float,
    ) -> list[Memory]:
        """ANN search via Qdrant, trust revalidation via PostgreSQL.

        Security invariant: every candidate returned by Qdrant is re-fetched
        from PostgreSQL and passed through is_eligible() before being returned.
        A memory's presence in Qdrant never implies it is trusted.
        """
        assert self._vector_store is not None
        # Fetch up to 4x candidates so we have headroom after the eligibility filter.
        candidate_limit = min(max_results * 4, 100)
        hits = self._vector_store.search_memories(
            query_vector=query_vector,
            embedding_model=self.embedder.model_name,
            top_k=candidate_limit,
            min_score=min_similarity,
        )
        if not hits:
            return []

        # Re-fetch canonical records from PostgreSQL.
        memory_uuids = [uuid.UUID(h.point_id) for h in hits]
        score_map = {h.point_id: h.score for h in hits}

        rows = self.db.scalars(
            select(Memory).where(
                Memory.id.in_(memory_uuids),
                # Pre-filter in SQL to avoid loading obviously ineligible rows.
                Memory.status == MemoryStatus.trusted,
                Memory.revoked_at.is_(None),
                or_(Memory.expires_at.is_(None), Memory.expires_at > datetime.now(UTC)),
                Memory.embedding_model == self.embedder.model_name,
            )
        ).all()

        # is_eligible() is the canonical trust gate — always enforced in Python.
        eligible = [
            m for m in rows if is_eligible(m) and score_map.get(str(m.id), 0.0) >= min_similarity
        ]
        # Preserve Qdrant's relevance ordering.
        eligible.sort(key=lambda m: score_map.get(str(m.id), 0.0), reverse=True)
        return eligible[:max_results]

    # ── Legacy path (pgvector SQL + Python fallback) ───────────────────────

    def _retrieve_legacy(
        self,
        query_vector: list[float],
        *,
        max_results: int,
        min_similarity: float,
    ) -> list[Memory]:
        """Original dual-path implementation: pgvector SQL on PostgreSQL, Python on SQLite.

        Preserved so existing unit and integration tests pass without modification.
        This path is not used in production after the Qdrant migration.
        """
        # Similarity only identifies candidates; canonical SQL checks remain authoritative.
        statement = select(Memory).where(
            Memory.embedding.is_not(None),
            Memory.embedding_model == self.embedder.model_name,
            Memory.status == MemoryStatus.trusted,
            Memory.revoked_at.is_(None),
            or_(Memory.expires_at.is_(None), Memory.expires_at > datetime.now(UTC)),
        )
        candidates: list[Memory]
        if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
            candidates = [m for m in self.db.scalars(statement).all() if m.embedding is not None]
            candidates.sort(key=lambda memory: _cosine_distance(memory.embedding, query_vector))
            candidates = candidates[: min(max_results * 4, 100)]
        else:
            candidates = list(
                self.db.scalars(
                    statement.order_by(Memory.embedding.cosine_distance(query_vector)).limit(
                        min(max_results * 4, 100)
                    )
                ).all()
            )
        eligible = [
            m
            for m in candidates
            if is_eligible(m) and 1 - _cosine_distance(m.embedding, query_vector) >= min_similarity
        ]
        return eligible[:max_results]


def is_eligible(memory: Memory, now: datetime | None = None) -> bool:
    """Canonical state validation shared by retrieval and its tests.

    This function is the authoritative trust gate.  It must be called on every
    memory candidate before the candidate is returned to the caller, regardless
    of whether the candidate came from Qdrant or from a pgvector SQL query.
    """
    now = now or datetime.now(UTC)
    expiry = memory.expires_at
    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    return (
        memory.status == MemoryStatus.trusted
        and (expiry is None or expiry > now)
        and memory.revoked_at is None
        and isinstance(memory.provenance, dict)
        and (
            memory.provenance.get("trust_level") in {"internal", "verified"}
            or memory.provenance.get("review_verified") is True
        )
    )


def _cosine_distance(left: list[float], right: list[float]) -> float:
    """Cosine distance for the legacy Python-side fallback ranker."""
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 1.0
    return 1.0 - dot / (left_norm * right_norm)
