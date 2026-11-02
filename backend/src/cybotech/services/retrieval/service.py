"""
Trusted memory retrieval via Qdrant ANN search + PostgreSQL canonical state revalidation.

Flow:
  1. Embed the query string.
  2. Ask Qdrant for the top-N nearest memory vectors (ANN, fast).
  3. Re-fetch those Memory rows from PostgreSQL and re-run is_eligible() on each one.
     This is the security step: Qdrant only identifies candidates; the SQL canonical
     state check is the authoritative gate.  A memory quarantined after indexing
     never reaches the caller.
  4. Filter by min_similarity and return up to max_results.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Protocol

from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from cybotech.infrastructure.database.models import Memory, MemoryStatus
from cybotech.infrastructure.vector.qdrant import QdrantStore


class Embedder(Protocol):
    model_name: str

    def embed(self, text: str) -> list[float]: ...


class OpenAIEmbedder:
    """
    Embedding adapter for OpenAI API and local servers (Ollama, LM Studio).

    For Ollama, pull an embedding model first:
        ollama pull nomic-embed-text   → 768-dim (use dimensions=768 or let it truncate)
        ollama pull mxbai-embed-large  → 1024-dim
        ollama pull all-minilm         → 384-dim (matches our vector size exactly)

    The simplest option: use all-minilm which is 384-dim by default.
    With nomic-embed-text, set OPENAI_EMBED_MODEL=nomic-embed-text and
    VECTOR_DIMENSIONS=768 — or just use all-minilm to keep 384 unchanged.

    For OpenAI, text-embedding-3-small with dimensions=384 works perfectly.
    """

    def __init__(self, client: OpenAI, model_name: str = "text-embedding-3-small") -> None:
        self.client = client
        self.model_name = model_name

    def embed(self, text: str) -> list[float]:
        # dimensions=384 is supported by OpenAI text-embedding-3-small.
        # For Ollama models, omit dimensions and let the model return its native size.
        import os
        model = os.getenv("OPENAI_EMBED_MODEL", self.model_name)
        kwargs: dict = {"model": model, "input": text}
        # Only pass dimensions for OpenAI API (Ollama ignores it anyway but avoids errors)
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        if "openai.com" in base_url:
            kwargs["dimensions"] = 384
        response = self.client.embeddings.create(**kwargs)
        return list(response.data[0].embedding)


class RetrievalService:
    """
    Memory retrieval backed by Qdrant vector search.

    Qdrant performs approximate nearest-neighbour search over the
    cybotech_memories collection.  Every candidate is then re-validated
    against the PostgreSQL canonical state before being returned,
    ensuring that quarantined or revoked memories are never surfaced
    even if they are still present in the Qdrant index.
    """

    def __init__(
        self,
        db: Session,
        embedder: Embedder,
        vector_store: QdrantStore,
        dimensions: int = 384,
    ) -> None:
        self.db = db
        self.embedder = embedder
        self.vector_store = vector_store
        self.dimensions = dimensions

    def retrieve(
        self,
        query: str,
        *,
        max_results: int = 5,
        principal: str = "primary_assistant",
        min_similarity: float = 0.0,
    ) -> list[Memory]:
        if principal != "primary_assistant":
            raise PermissionError("retrieval principal not permitted")
        if not 1 <= max_results <= 100 or not 0 <= min_similarity <= 1:
            raise ValueError("invalid retrieval bounds")
        if not query.strip():
            return []

        vector = self.embedder.embed(query)
        if len(vector) != self.dimensions:
            raise ValueError(f"embedding dimension must be {self.dimensions}")

        # Step 1 — Qdrant ANN: fetch a generous candidate pool filtered by
        # embedding_model so we never cross vector spaces (e.g. offline vs live).
        qdrant_results = self.vector_store.search_memories(
            vector=vector,
            top_k=min(max_results * 4, 100),
            filter_payload={"embedding_model": self.embedder.model_name},
        )
        if not qdrant_results:
            return []

        # Step 2 — PostgreSQL canonical revalidation: similarity score alone
        # is NOT a trust signal.  Re-fetch by UUID and re-run is_eligible().
        candidate_ids = [uuid.UUID(hit_id) for hit_id, _score, _payload in qdrant_results]
        score_map = {hit_id: score for hit_id, score, _payload in qdrant_results}

        rows = list(
            self.db.scalars(
                select(Memory).where(
                    Memory.id.in_(candidate_ids),  # type: ignore[arg-type]
                    Memory.status == MemoryStatus.trusted,
                    Memory.embedding_model == self.embedder.model_name,
                )
            ).all()
        )

        eligible = [
            row
            for row in rows
            if is_eligible(row) and score_map.get(str(row.id), 0.0) >= min_similarity
        ]

        # Preserve Qdrant's similarity ranking.
        eligible.sort(key=lambda m: score_map.get(str(m.id), 0.0), reverse=True)
        return eligible[:max_results]

    def index_memory(
        self,
        memory: Memory,
        vector: list[float],
    ) -> None:
        """
        Upsert a memory vector into Qdrant after it has been committed to PostgreSQL.
        Called by MemoryGuard when a memory transitions to trusted status.
        """
        self.vector_store.upsert_memory(
            memory_id=str(memory.id),
            vector=vector,
            payload={
                "embedding_model": self.embedder.model_name,
                "category": memory.category,
                "source_type": memory.source_type,
            },
        )

    def remove_memory(self, memory_id: str) -> None:
        """Remove a memory vector from Qdrant (quarantine / revocation)."""
        self.vector_store.delete_memory(memory_id)


def is_eligible(memory: Memory, now: datetime | None = None) -> bool:
    """Canonical state validation shared by retrieval and its tests."""
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
