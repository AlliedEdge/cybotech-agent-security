"""
reconcile-vectors — detect and repair missing or stale Qdrant vectors.

Usage
-----
    cd backend && uv run python -m cybotech.cli.reconcile_vectors [OPTIONS]

Options
-------
--db-url URL          PostgreSQL connection URL (default: DATABASE_URL env var).
--qdrant-url URL      Qdrant base URL (default: QDRANT_URL env var).
--qdrant-api-key KEY  Qdrant API key (default: QDRANT_API_KEY env var).
--repair              Re-upsert missing vectors found during reconciliation.
--batch-size N        Records per scroll batch (default: 256).
--collection {documents,memories,both}
                      Which collection to reconcile (default: both).

What this command does
----------------------
1. Fetches all canonical PostgreSQL record IDs that should have a Qdrant vector
   (document chunks with embeddings, memories with embeddings).
2. Computes the expected deterministic Qdrant point ID for each.
3. Queries Qdrant for which point IDs actually exist.
4. Reports missing and extra points.
5. With --repair: re-upserts missing points from PostgreSQL embedding data.

This command is READ-ONLY without --repair.

Exit codes
----------
0  No discrepancies found (or all repaired successfully).
1  Discrepancies found but not repaired (run with --repair to fix).
2  Fatal error.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from cybotech.infrastructure.database.models import DocumentChunk, DocumentRecord, Memory
from cybotech.infrastructure.vector_store.collections import (
    document_collection_config,
    memory_collection_config,
)
from cybotech.infrastructure.vector_store.interface import (
    DocumentChunkPoint,
    MemoryPoint,
    VectorStore,
)
from cybotech.infrastructure.vector_store.qdrant_adapter import (
    QdrantVectorStore,
    _point_id_for_chunk,
    _point_id_for_memory,
)


def _load_qdrant(
    qdrant_url: str, api_key: str | None
) -> tuple[VectorStore, object, Callable[[str], str], Callable[[str], str]]:
    from qdrant_client import QdrantClient

    from cybotech.infrastructure.vector_store.collections import (
        document_collection_config,
        memory_collection_config,
    )

    client = QdrantClient(url=qdrant_url, api_key=api_key or None, timeout=30)
    vs = QdrantVectorStore(
        client=client,
        document_config=document_collection_config(),
        memory_config=memory_collection_config(),
    )
    return vs, client, _point_id_for_chunk, _point_id_for_memory


def _scroll_all_point_ids(client: object, collection_name: str, batch: int = 100) -> set[str]:
    """Retrieve all point IDs from a Qdrant collection using the scroll API."""

    ids: set[str] = set()
    offset = None
    while True:
        result, next_offset = client.scroll(  # type: ignore[attr-defined]
            collection_name=collection_name,
            limit=batch,
            offset=offset,
            with_payload=False,
            with_vectors=False,
        )
        for point in result:
            ids.add(str(point.id))
        if next_offset is None:
            break
        offset = next_offset
    return ids


def reconcile_documents(
    db: Session,
    vs: VectorStore,
    client: object,
    point_id_for_chunk: Callable[[str], str],
    batch_size: int,
    repair: bool,
) -> dict[str, int]:

    doc_cfg = document_collection_config()
    print(f"\n[documents] Scrolling Qdrant collection '{doc_cfg.name}'...")
    qdrant_ids = _scroll_all_point_ids(client, doc_cfg.name)
    print(f"  Qdrant has {len(qdrant_ids)} points.")

    missing: list[tuple[DocumentChunk, DocumentRecord]] = []
    total_pg = 0
    offset = 0

    while True:
        rows = db.execute(
            select(DocumentChunk, DocumentRecord)
            .join(DocumentRecord, DocumentChunk.document_id == DocumentRecord.id)
            .where(DocumentChunk.embedding.is_not(None))
            .order_by(DocumentChunk.id)
            .offset(offset)
            .limit(batch_size)
        ).all()
        if not rows:
            break
        total_pg += len(rows)
        for chunk, doc in rows:
            expected_id = point_id_for_chunk(str(chunk.id))
            if expected_id not in qdrant_ids:
                missing.append((chunk, doc))
        offset += batch_size

    print(f"  PostgreSQL has {total_pg} chunks with embeddings.")
    print(f"  Missing from Qdrant: {len(missing)}")

    if missing and repair:
        print(f"  Repairing {len(missing)} missing chunks...")
        points = []
        for chunk, doc in missing:
            vec = (
                list(chunk.embedding) if not isinstance(chunk.embedding, list) else chunk.embedding
            )
            points.append(
                DocumentChunkPoint(
                    chunk_id=str(chunk.id),
                    document_id=str(chunk.document_id),
                    session_id=doc.session_id,
                    content_hash=chunk.content_hash,
                    embedding_model=chunk.embedding_model or "unknown",
                    embedding_version="reconciled-v1",
                    vector=vec,
                )
            )
            if len(points) >= batch_size:
                vs.upsert_document_chunks(points)
                points = []
        if points:
            vs.upsert_document_chunks(points)
        print(f"  Repaired {len(missing)} chunks.")

    return {"total_pg": total_pg, "qdrant_count": len(qdrant_ids), "missing": len(missing)}


def reconcile_memories(
    db: Session,
    vs: VectorStore,
    client: object,
    point_id_for_memory: Callable[[str], str],
    batch_size: int,
    repair: bool,
) -> dict[str, int]:

    mem_cfg = memory_collection_config()
    print(f"\n[memories] Scrolling Qdrant collection '{mem_cfg.name}'...")
    qdrant_ids = _scroll_all_point_ids(client, mem_cfg.name)
    print(f"  Qdrant has {len(qdrant_ids)} points.")

    missing: list[Memory] = []
    total_pg = 0
    offset = 0

    while True:
        rows = db.scalars(
            select(Memory)
            .where(Memory.embedding.is_not(None))
            .order_by(Memory.id)
            .offset(offset)
            .limit(batch_size)
        ).all()
        if not rows:
            break
        total_pg += len(rows)
        for row in rows:
            expected_id = point_id_for_memory(str(row.id))
            if expected_id not in qdrant_ids:
                missing.append(row)
        offset += batch_size

    print(f"  PostgreSQL has {total_pg} memories with embeddings.")
    print(f"  Missing from Qdrant: {len(missing)}")

    if missing and repair:
        print(f"  Repairing {len(missing)} missing memories...")
        for row in missing:
            vec = list(row.embedding) if not isinstance(row.embedding, list) else row.embedding
            vs.upsert_memory(
                MemoryPoint(
                    memory_id=str(row.id),
                    session_id="",
                    provenance_reference=row.source_id or "",
                    content_hash=row.provenance.get("content_sha256", ""),
                    embedding_model=row.embedding_model or "unknown",
                    embedding_version="reconciled-v1",
                    vector=vec,
                )
            )
        print(f"  Repaired {len(missing)} memories.")

    return {"total_pg": total_pg, "qdrant_count": len(qdrant_ids), "missing": len(missing)}


def run_reconciliation(args: argparse.Namespace) -> int:
    db_url = args.db_url or os.getenv("DATABASE_URL") or ""
    if not db_url:
        print("ERROR: --db-url or DATABASE_URL env var required.", file=sys.stderr)
        return 2

    qdrant_url = args.qdrant_url or os.getenv("QDRANT_URL", "http://localhost:6333")
    api_key = args.qdrant_api_key or os.getenv("QDRANT_API_KEY") or None
    collection = args.collection

    print(f"[reconcile-vectors] PostgreSQL: {db_url[:40]}...")
    print(f"[reconcile-vectors] Qdrant:     {qdrant_url}")
    if args.repair:
        print("[reconcile-vectors] REPAIR mode — missing vectors will be re-upserted.")
    else:
        print("[reconcile-vectors] READ-ONLY mode — pass --repair to fix discrepancies.")

    try:
        engine = create_engine(db_url, pool_pre_ping=True)
        vs, client, point_id_for_chunk, point_id_for_memory = _load_qdrant(qdrant_url, api_key)

        total_missing = 0
        with Session(engine, expire_on_commit=False) as db:
            if collection in {"documents", "both"}:
                doc_result = reconcile_documents(
                    db, vs, client, point_id_for_chunk, args.batch_size, args.repair
                )
                total_missing += doc_result["missing"]

            if collection in {"memories", "both"}:
                mem_result = reconcile_memories(
                    db, vs, client, point_id_for_memory, args.batch_size, args.repair
                )
                total_missing += mem_result["missing"]

    except Exception as exc:
        print(f"\nFATAL ERROR: {exc}", file=sys.stderr)
        return 2

    print("\n[reconcile-vectors] Done.")
    if total_missing > 0 and not args.repair:
        print(
            f"  Found {total_missing} missing vector(s). Run with --repair to fix.",
            file=sys.stderr,
        )
        return 1
    if total_missing > 0 and args.repair:
        print(f"  Repaired {total_missing} missing vector(s).")
    else:
        print("  No discrepancies found.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="reconcile-vectors",
        description="Detect and optionally repair missing Qdrant vectors.",
    )
    parser.add_argument("--db-url", default=None)
    parser.add_argument("--qdrant-url", default=None)
    parser.add_argument("--qdrant-api-key", default=None)
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument(
        "--collection",
        choices=["documents", "memories", "both"],
        default="both",
    )
    args = parser.parse_args()
    sys.exit(run_reconciliation(args))


if __name__ == "__main__":
    main()
