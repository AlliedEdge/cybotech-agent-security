import uuid

from cybotech.infrastructure.database.models import Memory, MemoryStatus
from cybotech.services.memory_guard.service import MemoryGuard

from .conftest import FixedEmbedder, memory_proposal


def test_memory_proposal_cannot_write_trusted_memory_without_review(db):
    guard = MemoryGuard(
        db, lambda *_: {"source_type": "email", "trust_level": "untrusted"}, FixedEmbedder()
    )
    decision = guard.propose_memory(memory_proposal())
    record = db.get(Memory, uuid.UUID(decision.proposal_id))
    assert decision.status == "quarantined"
    assert record is not None
    assert record.status is MemoryStatus.quarantined
