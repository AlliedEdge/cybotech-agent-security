"""
Qdrant collection configuration.

All collection parameters are defined here so they are easy to audit and
change in one place. Application code must not hard-code collection names or
vector parameters.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class CollectionConfig:
    """Immutable configuration for a single Qdrant collection."""

    name: str
    dimension: int
    # Embedding model that produced the vectors in this collection.
    # Stored as a payload field on every point and used to reject mismatches.
    embedding_model: str
    # Qdrant distance metric. Cosine is correct for L2-normalised embeddings
    # produced by text-embedding-3-small (dimensions=384) and LocalEmbedder.
    distance: str = "Cosine"
    # HNSW parameters — tuned for the expected dataset size in this deployment.
    # m=16 and ef_construct=100 are Qdrant defaults; adjust for scale.
    hnsw_m: int = 16
    hnsw_ef_construct: int = 100
    # Payload fields that will have indexes created for efficient filtering.
    indexed_payload_fields: tuple[str, ...] = ()


def document_collection_config() -> CollectionConfig:
    """Return the config for the document chunks collection."""
    return CollectionConfig(
        name=os.getenv("QDRANT_DOCUMENT_COLLECTION", "cybotech_document_chunks"),
        dimension=int(os.getenv("QDRANT_EMBEDDING_DIMENSIONS", "384")),
        embedding_model=os.getenv("CYBOTECH_EMBEDDING_MODEL", "text-embedding-3-small"),
        distance="Cosine",
        indexed_payload_fields=("session_id", "embedding_model", "document_id"),
    )


def memory_collection_config() -> CollectionConfig:
    """Return the config for the memory vectors collection."""
    return CollectionConfig(
        name=os.getenv("QDRANT_MEMORY_COLLECTION", "cybotech_memory_vectors"),
        dimension=int(os.getenv("QDRANT_EMBEDDING_DIMENSIONS", "384")),
        embedding_model=os.getenv("CYBOTECH_EMBEDDING_MODEL", "text-embedding-3-small"),
        distance="Cosine",
        indexed_payload_fields=("embedding_model", "memory_id"),
    )
