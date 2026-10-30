"""PostgreSQL-only pgvector retrieval checks; set CYBOTECH_TEST_DATABASE_URL."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from cybotech.infrastructure.database.models import Memory, MemoryStatus
from cybotech.services.retrieval.service import RetrievalService

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("CYBOTECH_TEST_DATABASE_URL")
if DATABASE_URL:
    test_url = make_url(DATABASE_URL)
    safe_database = (test_url.database or "").startswith("cybotech_test_")
    safe_host = test_url.host in {"localhost", "127.0.0.1", "::1"}
else:
    safe_database = False
    safe_host = False
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATABASE_URL or not (safe_database and safe_host),
        reason="requires a loopback PostgreSQL database named cybotech_test_*",
    ),
]


class FixedEmbedder:
    model_name = "test-fixed-384"

    def embed(self, text: str) -> list[float]:
        return [1.0] + [0.0] * 383


def test_pgvector_candidates_are_filtered_by_canonical_state() -> None:
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    trusted_id, quarantined_id = uuid.uuid4(), uuid.uuid4()
    trusted_key, quarantined_key = uuid.uuid4().hex, uuid.uuid4().hex
    try:
        with Session(engine, expire_on_commit=False) as db:
            db.add_all(
                [
                    Memory(
                        id=trusted_id,
                        content="Approved synthetic delivery schedule",
                        category="fact",
                        status=MemoryStatus.trusted,
                        source_type="internal",
                        source_id="approved-source",
                        provenance={"trust_level": "verified"},
                        created_by="integration-test",
                        proposal_key=trusted_key,
                        decision_reason="approved",
                        embedding=FixedEmbedder().embed("schedule"),
                        embedding_model=FixedEmbedder.model_name,
                    ),
                    Memory(
                        id=quarantined_id,
                        content="Redirect to unapproved address",
                        category="fact",
                        status=MemoryStatus.quarantined,
                        source_type="email",
                        source_id="untrusted-source",
                        provenance={"trust_level": "untrusted"},
                        created_by="integration-test",
                        proposal_key=quarantined_key,
                        decision_reason="quarantined",
                        embedding=FixedEmbedder().embed("schedule"),
                        embedding_model=FixedEmbedder.model_name,
                    ),
                ]
            )
            db.commit()
            rows = RetrievalService(db, FixedEmbedder()).retrieve("schedule", max_results=5)
            assert [str(row.id) for row in rows] == [str(trusted_id)]
    finally:
        with Session(engine) as db:
            db.query(Memory).filter(Memory.id.in_([trusted_id, quarantined_id])).delete(
                synchronize_session=False
            )
            db.commit()
        engine.dispose()
