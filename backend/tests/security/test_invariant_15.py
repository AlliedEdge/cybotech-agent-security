import pytest

from cybotech.api.v1.security import stream_audit_events
from cybotech.infrastructure.database.models import AuditEvent


class _Disconnect:
    calls = 0

    async def is_disconnected(self):
        self.calls += 1
        return self.calls > 1


@pytest.mark.asyncio
async def test_persisted_events_replay_in_order_after_an_sse_disconnect(db):
    db.add_all(
        [
            AuditEvent(
                event_id="evt-first", event_type="gateway_decision", actor="test", payload={}
            ),
            AuditEvent(
                event_id="evt-second",
                event_type="tool_execution_completed",
                actor="test",
                payload={},
            ),
        ]
    )
    db.commit()
    frames = [frame async for frame in stream_audit_events(_Disconnect(), db, "evt-first")]
    assert len(frames) == 1
    assert frames[0].startswith("id: evt-second")
