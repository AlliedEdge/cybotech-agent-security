from sqlalchemy import select

from cybotech.infrastructure.database.models import AuditEvent
from cybotech.services.memory_guard.service import MemoryGuard
from cybotech.services.policy_gateway.service import PolicyGateway

from .conftest import completed_finding, memory_proposal, report_action


def test_security_decisions_persist_audit_evidence(db):
    MemoryGuard(db, lambda *_: None).propose_memory(memory_proposal())
    PolicyGateway(db).authorize(report_action(), sentinel_finding=completed_finding())
    event_types = set(db.scalars(select(AuditEvent.event_type)).all())
    assert {"memory_guard_decision", "gateway_decision"} <= event_types
