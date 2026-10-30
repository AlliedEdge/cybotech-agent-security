from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from cybotech.agents.contracts import AgentTurn, ModelAction
from cybotech.infrastructure.database.models import AuditEvent, Base
from cybotech.infrastructure.database.session import get_db
from cybotech.infrastructure.llm.providers import ScriptedProvider
from cybotech.infrastructure.sandbox.tools import SandboxDispatcher
from cybotech.main import app
from cybotech.services.sentinel.service import Sentinel


class FakeEmbedder:
    model_name = "test-384"

    def embed(self, text: str) -> list[float]:
        return [1.0] + [0.0] * 383


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CYBOTECH_SECURITY_MODE", "local_demo")
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)

    def override_db():
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    engine.dispose()


def test_registered_assistant_uses_persistent_gateway_and_approval(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cybotech.api.v1 import security

    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(security, "OpenAI", lambda **_kwargs: object())
    monkeypatch.setattr(security, "OpenAIEmbedder", lambda _client: FakeEmbedder())
    monkeypatch.setattr(
        security,
        "OpenAIProvider",
        lambda _client, _model: ScriptedProvider(
            [
                AgentTurn(
                    reply="Report prepared",
                    memories=[],
                    actions=[
                        ModelAction(
                            tool_name="send_report",
                            arguments_json=(
                                '{"destination":"reports@internal.example",'
                                '"content":"synthetic report"}'
                            ),
                        )
                    ],
                    done=True,
                )
            ]
        ),
    )

    execution_calls = 0
    original_execute = SandboxDispatcher.execute

    def tracked_execute(dispatcher, proposal):
        nonlocal execution_calls
        execution_calls += 1
        return original_execute(dispatcher, proposal)

    monkeypatch.setattr(SandboxDispatcher, "execute", tracked_execute)
    run = {
        "task": "Send the weekly report",
        "session_id": "session-1",
        "task_id": "task-1",
        "documents": [],
    }

    denied = client.post("/api/v1/assistant/run", json=run)
    assert denied.status_code == 200
    assert execution_calls == 0
    assert denied.json()["tool_results"] == []

    approval_payload = {
        "tool_name": "send_report",
        "arguments": {
            "destination": "reports@internal.example",
            "content": "synthetic report",
        },
        "proposed_by": "primary_assistant",
        "session_id": "session-1",
        "task_id": "task-1",
        "user_task_description": "Send the weekly report",
        "retrieved_memory_ids": [],
    }
    approval = client.post("/api/v1/approvals", json=approval_payload)
    assert approval.status_code == 200
    approval_id = approval.json()["id"]
    decided = client.post(
        f"/api/v1/approvals/{approval_id}/decision",
        json={"approved": True, "reviewer": "local_demo_reviewer"},
    )
    assert decided.status_code == 200

    run["approval_id"] = approval_id
    authorized = client.post("/api/v1/assistant/run", json=run)
    assert authorized.status_code == 200
    assert execution_calls == 1
    assert authorized.json()["tool_results"][0]["success"] is True

    replayed_approval = client.post("/api/v1/assistant/run", json=run)
    assert replayed_approval.status_code == 200
    assert execution_calls == 1
    assert replayed_approval.json()["tool_results"] == []

    events = client.get("/api/v1/security-events?limit=100").json()["items"]
    assert any(
        event["type"] == "gateway_decision" and event["outcome"] == "denied" for event in events
    )
    assert any(
        event["type"] == "gateway_decision" and event["outcome"] == "allowed" for event in events
    )
    assert any(
        event["type"] == "tool_execution_completed" and event["outcome"] == "True"
        for event in events
    )


@pytest.mark.parametrize("failure", ["timeout", "malformed"])
def test_registered_assistant_reviewer_failure_never_calls_executor(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from cybotech.api.v1 import security

    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(security, "OpenAI", lambda **_kwargs: object())
    monkeypatch.setattr(security, "OpenAIEmbedder", lambda _client: FakeEmbedder())
    monkeypatch.setattr(
        security,
        "OpenAIProvider",
        lambda _client, _model: ScriptedProvider(
            [
                AgentTurn(
                    reply="Proposed report",
                    memories=[],
                    actions=[
                        ModelAction(
                            tool_name="read_email",
                            arguments_json='{"id":"synthetic-email-1"}',
                        )
                    ],
                    done=True,
                )
            ]
        ),
    )

    def reviewer_failure(_self, _proposal):
        if failure == "timeout":
            raise TimeoutError("review timed out")
        return {"status": "completed", "recommendation": "PROCEED"}

    monkeypatch.setattr(Sentinel, "review", reviewer_failure)
    calls = 0
    original_execute = SandboxDispatcher.execute

    def tracked_execute(dispatcher, proposal):
        nonlocal calls
        calls += 1
        return original_execute(dispatcher, proposal)

    monkeypatch.setattr(SandboxDispatcher, "execute", tracked_execute)
    response = client.post(
        "/api/v1/assistant/run",
        json={
            "task": "Read the vendor email",
            "session_id": "session-review-failure",
            "task_id": f"task-review-{failure}",
            "documents": [],
        },
    )
    assert response.status_code == 200
    assert calls == 0
    assert response.json()["tool_results"] == []


def test_audit_commit_failure_fails_closed_before_tool_execution(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cybotech.api.v1 import security

    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(security, "OpenAI", lambda **_kwargs: object())
    monkeypatch.setattr(security, "OpenAIEmbedder", lambda _client: FakeEmbedder())
    monkeypatch.setattr(
        security,
        "OpenAIProvider",
        lambda _client, _model: ScriptedProvider(
            [
                AgentTurn(
                    reply="Proposed read",
                    memories=[],
                    actions=[
                        ModelAction(
                            tool_name="read_email",
                            arguments_json='{"id":"synthetic-email-1"}',
                        )
                    ],
                    done=True,
                )
            ]
        ),
    )
    calls = 0
    original_execute = SandboxDispatcher.execute

    def tracked_execute(dispatcher, proposal):
        nonlocal calls
        calls += 1
        return original_execute(dispatcher, proposal)

    monkeypatch.setattr(SandboxDispatcher, "execute", tracked_execute)

    def fail_gateway_audit(session):
        if any(
            isinstance(row, AuditEvent) and row.event_type == "gateway_decision"
            for row in session.new
        ):
            raise RuntimeError("synthetic audit storage failure")

    event.listen(Session, "before_commit", fail_gateway_audit)
    try:
        with TestClient(app, raise_server_exceptions=False) as isolated_client:
            response = isolated_client.post(
                "/api/v1/assistant/run",
                json={
                    "task": "Read the vendor email",
                    "session_id": "session-audit-failure",
                    "task_id": "task-audit-failure",
                    "documents": [],
                },
            )
    finally:
        event.remove(Session, "before_commit", fail_gateway_audit)
    assert response.status_code == 500
    assert calls == 0
