from cybotech.infrastructure.database.models import MemoryStatus
from cybotech.services.retrieval.service import RetrievalService

from .conftest import FixedEmbedder, index_in_qdrant, stored_memory


def test_retrieval_rechecks_canonical_state_after_candidate_selection(db, vector_store):
    trusted = stored_memory(
        key="trusted", status=MemoryStatus.trusted, provenance={"trust_level": "verified"}
    )
    quarantined = stored_memory(
        key="quarantined",
        status=MemoryStatus.quarantined,
        provenance={"trust_level": "verified"},
    )
    db.add_all([trusted, quarantined])
    db.commit()
    index_in_qdrant(vector_store, trusted)
    index_in_qdrant(vector_store, quarantined)
    assert [
        row.proposal_key for row in RetrievalService(db, FixedEmbedder(), vector_store).retrieve("synthetic")
    ] == ["trusted"]
