"""
Vector-store abstraction package.

Exports the port interface and typed contracts so that application services
depend only on this module — never directly on the Qdrant client.
"""

from cybotech.infrastructure.vector_store.interface import (
    DocumentChunkPoint,
    MemoryPoint,
    SearchResult,
    VectorDimensionError,
    VectorModelMismatchError,
    VectorStore,
    VectorStoreError,
)

__all__ = [
    "DocumentChunkPoint",
    "MemoryPoint",
    "SearchResult",
    "VectorDimensionError",
    "VectorModelMismatchError",
    "VectorStore",
    "VectorStoreError",
]
