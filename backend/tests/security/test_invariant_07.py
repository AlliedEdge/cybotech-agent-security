from cybotech.infrastructure.database.models import MemoryStatus
from cybotech.services.retrieval.service import RetrievalService

from .conftest import FixedEmbedder, stored_memory


def test_retrieval_rechecks_canonical_state_after_candidate_selection(db):
    db.add_all(
        [
            stored_memory(
                key="trusted", status=MemoryStatus.trusted, provenance={"trust_level": "verified"}
            ),
            stored_memory(
                key="quarantined",
                status=MemoryStatus.quarantined,
                provenance={"trust_level": "verified"},
            ),
        ]
    )
    db.commit()
    assert [
        row.proposal_key for row in RetrievalService(db, FixedEmbedder()).retrieve("synthetic")
    ] == ["trusted"]
