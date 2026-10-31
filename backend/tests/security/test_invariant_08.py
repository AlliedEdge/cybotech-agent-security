from datetime import UTC, datetime, timedelta

from cybotech.infrastructure.database.models import MemoryStatus
from cybotech.services.retrieval.service import RetrievalService

from .conftest import FixedEmbedder, stored_memory


def test_nontrusted_expired_and_revoked_memories_never_reach_retrieval(db):
    db.add_all(
        [
            stored_memory(
                key="quarantined",
                status=MemoryStatus.quarantined,
                provenance={"trust_level": "verified"},
            ),
            stored_memory(
                key="rejected", status=MemoryStatus.rejected, provenance={"trust_level": "verified"}
            ),
            stored_memory(
                key="expired",
                status=MemoryStatus.trusted,
                provenance={"trust_level": "verified"},
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            ),
            stored_memory(
                key="revoked",
                status=MemoryStatus.revoked,
                provenance={"trust_level": "verified"},
                revoked_at=datetime.now(UTC),
            ),
        ]
    )
    db.commit()
    assert RetrievalService(db, FixedEmbedder()).retrieve("synthetic") == []
