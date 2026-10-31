"""PostgreSQL/pgvector document retrieval; guarded by the test-DB URL policy."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from cybotech.services.documents.service import DocumentService

DATABASE_URL = os.getenv("CYBOTECH_TEST_DATABASE_URL")
url = make_url(DATABASE_URL) if DATABASE_URL else None
pytestmark = pytest.mark.skipif(
    not url
    or not (url.database or "").startswith("cybotech_test_")
    or url.host not in {"localhost", "127.0.0.1", "::1"},
    reason="requires a loopback PostgreSQL database named cybotech_test_*",
)


class FixedEmbedder:
    model_name = "postgres-document-test-384"

    def embed(self, text: str) -> list[float]:
        return [1.0 if "schedule" in text.lower() else 0.0] + [0.0] * 383


def test_pgvector_document_retrieval_is_session_scoped_and_honors_deletion() -> None:
    assert DATABASE_URL
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    try:
        with Session(engine) as db:
            service = DocumentService(db, FixedEmbedder())
            document = service.ingest(
                "schedule.txt",
                "text/plain",
                b"Approved synthetic delivery schedule.",
                "pg-session-a",
            )
            assert len(service.retrieve("schedule", "pg-session-a")) == 1
            assert service.retrieve("schedule", "pg-session-b") == []
            assert service.delete(document.id, "pg-session-a")
            assert service.retrieve("schedule", "pg-session-a") == []
    finally:
        engine.dispose()
