"""Shared, isolated fixtures for the documented security invariants."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from cybotech.agents.contracts import ActionProposal, MemoryProposal
from cybotech.infrastructure.database.models import Base, Memory, MemoryStatus
from cybotech.services.sentinel.service import Sentinel, SentinelFinding


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


class FixedEmbedder:
    model_name = "security-test-384"

    def __init__(self, vector: list[float] | None = None) -> None:
        self.vector = vector or [1.0] + [0.0] * 383

    def embed(self, _text: str) -> list[float]:
        return self.vector


def report_action(**changes) -> ActionProposal:
    values = {
        "tool_name": "send_report",
        "arguments": {"destination": "reports@internal.example", "content": "synthetic"},
        "proposed_by": "primary_assistant",
        "session_id": "security-session",
        "task_id": "security-task",
        "user_task_description": "Send the weekly report",
        "retrieved_memory_ids": [],
    }
    values.update(changes)
    return ActionProposal(**values)


def memory_proposal(**changes) -> MemoryProposal:
    values = {
        "content": "Synthetic internal support fact",
        "source_document_id": "source-1",
        "proposed_by": "primary_assistant",
        "claimed_category": "support_fact",
        "raw_context": "Synthetic source",
    }
    values.update(changes)
    return MemoryProposal(**values)


def stored_memory(*, key: str, status: MemoryStatus, provenance: dict, **changes) -> Memory:
    values = {
        "content": "Synthetic memory",
        "category": "fact",
        "status": status,
        "source_type": "internal",
        "source_id": "source-1",
        "provenance": provenance,
        "created_by": "security-test",
        "proposal_key": key,
        "decision_reason": "fixture",
        "embedding": [1.0] + [0.0] * 383,
        "embedding_model": FixedEmbedder.model_name,
    }
    values.update(changes)
    return Memory(**values)


def completed_finding() -> SentinelFinding:
    return Sentinel().review(report_action())


def approved_report(db, action: ActionProposal | None = None) -> tuple[ActionProposal, str]:
    from cybotech.services.policy_gateway.service import PolicyGateway

    action = action or report_action()
    gateway = PolicyGateway(db)
    approval_id = gateway.create_approval(
        action, "reviewer", datetime.now(UTC) + timedelta(minutes=1)
    )
    assert gateway.decide_approval(approval_id, "reviewer", True) == "approved"
    return action, approval_id
