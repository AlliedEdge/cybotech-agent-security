from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Protocol

from openai import OpenAI
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from cybotech.infrastructure.database.models import Memory, MemoryStatus


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
    def __init__(self, db: Session, embedder: Embedder, dimensions: int = 384) -> None:
        self.db, self.embedder, self.dimensions = db, embedder, dimensions

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
            candidates.sort(key=lambda memory: _cosine_distance(memory.embedding, vector))
            candidates = candidates[: min(max_results * 4, 100)]
        else:
            candidates = list(
                self.db.scalars(
                    statement.order_by(Memory.embedding.cosine_distance(vector)).limit(
                        min(max_results * 4, 100)
                    )
                ).all()
            )
        eligible = [
            m
            for m in candidates
            if is_eligible(m) and 1 - _cosine_distance(m.embedding, vector) >= min_similarity
        ]
        return eligible[:max_results]


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


def _cosine_distance(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 1.0
    return 1.0 - dot / (left_norm * right_norm)
