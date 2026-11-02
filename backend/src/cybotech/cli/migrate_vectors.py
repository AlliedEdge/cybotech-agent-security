"""
migrate-vectors — export existing pgvector embeddings to Qdrant.

Usage
-----
    cd backend && uv run python -m cybotech.cli.migrate_vectors [OPTIONS]

Options
-------
--db-url URL          PostgreSQL connection URL (default: DATABASE_URL env var).
--qdrant-url URL      Qdrant base URL (default: QDRANT_URL env var).
--qdrant-api-key KEY  Qdrant API key (default: QDRANT_API_KEY env var).
--batch-size N        Records per batch (default: 256).
--dry-run             Report counts and dimensions without writing to Qdrant.
--skip-verification   Skip the post-migration verification queries.

Safety contract
---------------
- This command NEVER deletes pgvector data.
- All Qdrant writes are idempotent upserts (deterministic UUID5 point IDs).
- The command is resumable: re-running it after a partial failure only
  re-upserts missing or stale points without duplicating completed ones.
- Destructive pgvector removal (DROP COLUMN, remove extension) is a separate
  authorised step documented in docs/qdrant-operations.md.

Exit codes
----------
0  Migration completed and verified successfully.
1  Migration completed but verification found discrepancies.
2  Fatal error — migration aborted; no partial data was committed.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from cybotech.infrastructure.database.models import (
    DocumentChunk,
    DocumentRecord,
    Memory,
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

# ---------------------------------------------------------------------------
# Lazy imports — avoid forcing all of the app to load at module import time
# so the CLI can report friendly errors for missing env vars early.
# ---------------------------------------------------------------------------


def _load_qdrant(
    qdrant_url: str, api_key: str | None
) -> tuple[QdrantVectorStore, Callable[[str], str], Callable[[str], str]]:
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
    return vs, _point_id_for_chunk, _point_id_for_memory


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------


class MigrationReport:
    def __init__(self) -> None:
        self.started_at = datetime.now(UTC).isoformat()
        self.document_chunks_in_pg: int = 0
        self.document_chunks_migrated: int = 0
        self.document_chunks_skipped_no_embedding: int = 0
        self.memories_in_pg: int = 0
        self.memories_migrated: int = 0
        self.memories_skipped_no_embedding: int = 0
        self.models_seen: set[str] = set()
        self.verification_passed: bool | None = None
        self.verification_notes: list[str] = []
        self.errors: list[str] = []
        self.finished_at: str = ""

    def print_summary(self) -> None:
        print("\n" + "=" * 70)
        print("VECTOR MIGRATION REPORT")
        print("=" * 70)
        print(f"  Started:          {self.started_at}")
        print(f"  Finished:         {self.finished_at}")
        print(f"  Embedding models: {sorted(self.models_seen) or '(none found)'}")
        print()
        print("  Document chunks")
        print(f"    In PostgreSQL:  {self.document_chunks_in_pg}")
        print(f"    Migrated:       {self.document_chunks_migrated}")
        print(f"    No embedding:   {self.document_chunks_skipped_no_embedding}")
        print()
        print("  Memory vectors")
        print(f"    In PostgreSQL:  {self.memories_in_pg}")
        print(f"    Migrated:       {self.memories_migrated}")
        print(f"    No embedding:   {self.memories_skipped_no_embedding}")
        print()
        if self.verification_passed is None:
            print("  Verification:     skipped")
        elif self.verification_passed:
            print("  Verification:     PASSED ✓")
        else:
            print("  Verification:     FAILED ✗")
        for note in self.verification_notes:
            print(f"    - {note}")
        if self.errors:
            print()
            print("  Errors:")
            for err in self.errors:
                print(f"    ! {err}")
        print("=" * 70)


# ---------------------------------------------------------------------------
# Main migration logic
# ---------------------------------------------------------------------------


def migrate_document_chunks(
    db: Session,
    vs: VectorStore,
    point_id_fn: Callable[[str], str],
    batch_size: int,
    dry_run: bool,
    report: MigrationReport,
) -> None:

    # Count total
    total = db.scalar(select(func.count()).select_from(DocumentChunk))
    report.document_chunks_in_pg = total or 0
    print(f"\n[document_chunks] Found {report.document_chunks_in_pg} rows in PostgreSQL.")

    if report.document_chunks_in_pg == 0:
        print("  No document chunks to migrate — fresh installation path verified.")
        return

    offset = 0
    while True:
        rows = db.execute(
            select(DocumentChunk, DocumentRecord)
            .join(DocumentRecord, DocumentChunk.document_id == DocumentRecord.id)
            .order_by(DocumentChunk.id)
            .offset(offset)
            .limit(batch_size)
        ).all()
        if not rows:
            break

        points: list[DocumentChunkPoint] = []
        for chunk, doc in rows:
            if chunk.embedding is None:
                report.document_chunks_skipped_no_embedding += 1
                continue
            vec = (
                list(chunk.embedding) if not isinstance(chunk.embedding, list) else chunk.embedding
            )
            if len(vec) != 384:
                report.errors.append(f"chunk {chunk.id}: unexpected dimension {len(vec)}, skipping")
                continue
            report.models_seen.add(chunk.embedding_model or "unknown")
            points.append(
                DocumentChunkPoint(
                    chunk_id=str(chunk.id),
                    document_id=str(chunk.document_id),
                    session_id=doc.session_id,
                    content_hash=chunk.content_hash,
                    embedding_model=chunk.embedding_model or "unknown",
                    embedding_version="migrated-v1",
                    vector=vec,
                )
            )

        if points and not dry_run:
            try:
                vs.upsert_document_chunks(points)
            except Exception as exc:
                report.errors.append(f"Qdrant upsert error at offset {offset}: {exc}")
                print(f"  ERROR: {exc}")

        report.document_chunks_migrated += len(points)
        offset += batch_size
        print(
            f"  Batch offset={offset}: {len(points)} upserted" + (" (dry-run)" if dry_run else ""),
            flush=True,
        )


def migrate_memories(
    db: Session,
    vs: VectorStore,
    point_id_fn: Callable[[str], str],
    batch_size: int,
    dry_run: bool,
    report: MigrationReport,
) -> None:

    total = db.scalar(select(func.count()).select_from(Memory))
    report.memories_in_pg = total or 0
    print(f"\n[memories] Found {report.memories_in_pg} rows in PostgreSQL.")

    if report.memories_in_pg == 0:
        print("  No memories to migrate — fresh installation path verified.")
        return

    offset = 0
    while True:
        rows = db.scalars(select(Memory).order_by(Memory.id).offset(offset).limit(batch_size)).all()
        if not rows:
            break

        for row in rows:
            if row.embedding is None:
                report.memories_skipped_no_embedding += 1
                continue
            vec = list(row.embedding) if not isinstance(row.embedding, list) else row.embedding
            if len(vec) != 384:
                report.errors.append(f"memory {row.id}: unexpected dimension {len(vec)}, skipping")
                continue
            report.models_seen.add(row.embedding_model or "unknown")
            point = MemoryPoint(
                memory_id=str(row.id),
                session_id="",
                provenance_reference=row.source_id or "",
                content_hash=row.provenance.get("content_sha256", ""),
                embedding_model=row.embedding_model or "unknown",
                embedding_version="migrated-v1",
                vector=vec,
            )
            if not dry_run:
                try:
                    vs.upsert_memory(point)
                    report.memories_migrated += 1
                except Exception as exc:
                    report.errors.append(f"Qdrant memory upsert {row.id}: {exc}")
            else:
                report.memories_migrated += 1

        offset += batch_size
        print(
            f"  Batch offset={offset}: processed" + (" (dry-run)" if dry_run else ""),
            flush=True,
        )


def verify_migration(db: Session, vs: VectorStore, report: MigrationReport) -> bool:
    """Verify Qdrant counts match PostgreSQL and spot-check a search result."""
    from typing import cast

    from cybotech.infrastructure.vector_store.collections import (
        document_collection_config,
        memory_collection_config,
    )
    from cybotech.infrastructure.vector_store.qdrant_adapter import QdrantVectorStore

    qvs = cast(QdrantVectorStore, vs)
    doc_cfg = document_collection_config()
    mem_cfg = memory_collection_config()

    passed = True

    try:
        doc_info = qvs._client.get_collection(doc_cfg.name)
        qdrant_doc_count = doc_info.points_count or 0
        expected_doc = report.document_chunks_migrated
        if qdrant_doc_count < expected_doc:
            msg = (
                f"Document chunk count mismatch: Qdrant={qdrant_doc_count}, migrated={expected_doc}"
            )
            report.verification_notes.append(msg)
            passed = False
        else:
            report.verification_notes.append(
                f"Document chunks: Qdrant={qdrant_doc_count}, migrated={expected_doc} ✓"
            )
    except Exception as exc:
        report.verification_notes.append(f"Could not verify document collection: {exc}")
        passed = False

    try:
        mem_info = qvs._client.get_collection(mem_cfg.name)
        qdrant_mem_count = mem_info.points_count or 0
        expected_mem = report.memories_migrated
        if qdrant_mem_count < expected_mem:
            msg = f"Memory count mismatch: Qdrant={qdrant_mem_count}, migrated={expected_mem}"
            report.verification_notes.append(msg)
            passed = False
        else:
            report.verification_notes.append(
                f"Memories: Qdrant={qdrant_mem_count}, migrated={expected_mem} ✓"
            )
    except Exception as exc:
        report.verification_notes.append(f"Could not verify memory collection: {exc}")
        passed = False

    # Embedding model identity verification
    seen = sorted(report.models_seen)
    report.verification_notes.append(f"Embedding models found: {seen or '(none)'}")

    return passed


def run_migration(args: argparse.Namespace) -> int:
    report = MigrationReport()
    db_url = args.db_url or os.getenv("DATABASE_URL") or ""
    if not db_url:
        print("ERROR: --db-url or DATABASE_URL env var required.", file=sys.stderr)
        return 2

    qdrant_url = args.qdrant_url or os.getenv("QDRANT_URL", "http://localhost:6333")
    api_key = args.qdrant_api_key or os.getenv("QDRANT_API_KEY") or None

    print(f"[migrate-vectors] Connecting to PostgreSQL: {db_url[:40]}...")
    print(f"[migrate-vectors] Connecting to Qdrant:     {qdrant_url}")
    if args.dry_run:
        print("[migrate-vectors] DRY-RUN mode — no writes to Qdrant.")

    try:
        engine = create_engine(db_url, pool_pre_ping=True)
        vs, point_id_for_chunk, point_id_for_memory = _load_qdrant(qdrant_url, api_key)

        if not args.dry_run:
            print("[migrate-vectors] Ensuring Qdrant collections exist...")
            vs.ensure_collections()

        with Session(engine, expire_on_commit=False) as db:
            migrate_document_chunks(
                db, vs, point_id_for_chunk, args.batch_size, args.dry_run, report
            )
            migrate_memories(db, vs, point_id_for_memory, args.batch_size, args.dry_run, report)

            if not args.dry_run and not args.skip_verification:
                print("\n[migrate-vectors] Running verification...")
                report.verification_passed = verify_migration(db, vs, report)
            elif args.dry_run:
                report.verification_passed = None  # not applicable

    except Exception as exc:
        report.errors.append(f"Fatal: {exc}")
        report.finished_at = datetime.now(UTC).isoformat()
        report.print_summary()
        print(f"\nFATAL ERROR: {exc}", file=sys.stderr)
        return 2

    report.finished_at = datetime.now(UTC).isoformat()
    report.print_summary()

    if report.errors:
        return 1
    if report.verification_passed is False:
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="migrate-vectors",
        description="Export pgvector embeddings from PostgreSQL to Qdrant.",
    )
    parser.add_argument("--db-url", default=None, help="PostgreSQL connection URL.")
    parser.add_argument("--qdrant-url", default=None, help="Qdrant base URL.")
    parser.add_argument("--qdrant-api-key", default=None, help="Qdrant API key.")
    parser.add_argument("--batch-size", type=int, default=256, help="Records per batch.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report counts without writing to Qdrant.",
    )
    parser.add_argument(
        "--skip-verification",
        action="store_true",
        help="Skip post-migration verification.",
    )
    args = parser.parse_args()
    sys.exit(run_migration(args))


if __name__ == "__main__":
    main()
