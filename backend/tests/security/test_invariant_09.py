from cybotech.infrastructure.database.models import MemoryStatus
from cybotech.services.retrieval.service import RetrievalService

from .conftest import FixedEmbedder, stored_memory


def test_high_similarity_does_not_establish_trust(db):
    db.add(
        stored_memory(
            key="untrusted-best-match",
            status=MemoryStatus.trusted,
            provenance={"trust_level": "untrusted"},
        )
    )
    db.commit()
    assert RetrievalService(db, FixedEmbedder()).retrieve("synthetic") == []
