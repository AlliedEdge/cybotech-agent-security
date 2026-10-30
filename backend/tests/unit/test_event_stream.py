from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from cybotech.api.v1.security import stream_audit_events
from cybotech.infrastructure.database.models import AuditEvent, Base


class DisconnectAfterBatch:
    def __init__(self) -> None:
        self.checks = 0

    async def is_disconnected(self) -> bool:
        self.checks += 1
        return self.checks > 1


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def _event(event_id: str, event_type: str, outcome: str) -> AuditEvent:
    return AuditEvent(
        event_id=event_id,
        event_type=event_type,
        actor="test",
        correlation_id="run-1",
        outcome=outcome,
        payload={"synthetic": True},
    )


@pytest.mark.asyncio
async def test_persisted_sse_frames_replay_after_last_event_id_and_stop_on_disconnect(db):
    first = _event("evt_1", "gateway_decision", "denied")
    second = _event("evt_2", "tool_execution_completed", "success")
    db.add_all([first, second])
    db.commit()
    assert first.occurred_at is not None

    request = DisconnectAfterBatch()
    frames = [frame async for frame in stream_audit_events(request, db, "evt_1")]
    assert len(frames) == 1
    assert frames[0].startswith("id: evt_2\nevent: tool_execution_completed\n")
    assert '"id": "evt_2"' in frames[0]
    assert '"timestamp":' in frames[0]
    assert request.checks == 2


@pytest.mark.asyncio
async def test_persisted_sse_has_keepalive_when_history_is_empty(db):
    frames = [frame async for frame in stream_audit_events(DisconnectAfterBatch(), db)]
    assert frames == [": keep-alive\n\n"]
