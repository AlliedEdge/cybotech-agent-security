from cybotech.services.memory_guard.service import MemoryGuard

from .conftest import memory_proposal


def test_missing_or_invalid_provenance_is_quarantined(db):
    decision = MemoryGuard(db, lambda *_: None).propose_memory(memory_proposal())
    assert (decision.status, decision.reason) == ("quarantined", "provenance_missing")
