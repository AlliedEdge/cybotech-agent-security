from collections.abc import Iterator

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from cybotech.api.v1.security import require_local_security_access
from cybotech.infrastructure.database.models import Base
from cybotech.infrastructure.database.session import get_db
from cybotech.main import app


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CYBOTECH_SECURITY_MODE", "local_demo")
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)

    def override_db():
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    engine.dispose()


def test_proposal_api_does_not_trust_caller_claim(api_client):
    response = api_client.post(
        "/api/v1/memories/proposals",
        json={
            "content": "The shipping desk closes at five",
            "category": "support_fact",
            "source_type": "internal",
            "source_id": "forged-source",
            "source_trust": "verified",
            "proposed_by": "primary_assistant",
            "source_context": "A user controlled source",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "quarantined"
    listed = api_client.get("/api/v1/memories?status=quarantined")
    assert listed.json()["items"][0]["provenance"]["trust_level"] == "untrusted"


def test_caller_cannot_assign_review_agent_identity(api_client):
    response = api_client.post(
        "/api/v1/memories/proposals",
        json={
            "content": "synthetic fact",
            "category": "fact",
            "source_type": "email",
            "source_id": "source",
            "proposed_by": "sentinel",
        },
    )
    assert response.status_code == 422


def test_registered_and_ingested_document_catalogs_are_distinct(api_client):
    import base64

    payload = {
        "title": "demo.txt",
        "content_type": "text/plain",
        "content_base64": base64.b64encode(b"SYNTHETIC retrieval content").decode(),
        "session_id": "session-a",
    }
    assert api_client.post("/api/v1/documents/ingest", json=payload).status_code == 200
    registered = api_client.get("/api/v1/documents").json()["items"]
    ingested = api_client.get("/api/v1/documents/ingested?session_id=session-a").json()["items"]
    assert registered
    assert ingested[0]["title"] == "demo.txt"
    assert api_client.get("/api/v1/documents/ingested?session_id=session-b").json()["items"] == []
    response = api_client.post(
        "/api/v1/assistant/run",
        json={"task": "retrieval content", "session_id": "session-a", "task_id": "test-rag"},
    )
    assert response.status_code == 200
    assert "SYNTHETIC retrieval content" in response.json()["reply"]
    memories = api_client.get("/api/v1/memories?status=quarantined").json()["items"]
    assert any("SYNTHETIC retrieval content" in memory["content"] for memory in memories)


def test_extra_demo_origins_must_be_loopback(api_client, monkeypatch):
    monkeypatch.setenv("CYBOTECH_LOCAL_ORIGINS", "http://127.0.0.1:5174,http://evil.example")
    assert (
        api_client.get("/api/v1/runtime", headers={"Origin": "http://127.0.0.1:5174"}).status_code
        == 200
    )
    assert (
        api_client.get("/api/v1/runtime", headers={"Origin": "http://evil.example"}).status_code
        == 403
    )


def test_generated_attack_requires_live_mode_and_known_attack(api_client, monkeypatch):
    monkeypatch.setenv("CYBOTECH_MODEL_MODE", "offline")
    assert (
        api_client.post(
            "/api/v1/lab/generated-attack", json={"case_id": "memory_poisoning-00"}
        ).status_code
        == 503
    )
    monkeypatch.setenv("CYBOTECH_MODEL_MODE", "live")
    assert (
        api_client.post("/api/v1/lab/generated-attack", json={"case_id": "benign-00"}).status_code
        == 422
    )


def test_generated_attack_reports_live_victim(api_client, monkeypatch):
    from cybotech.api.v1 import security
    from cybotech.services.red_team import graph

    monkeypatch.setenv("CYBOTECH_MODEL_MODE", "live")
    monkeypatch.setattr(security, "runtime_client", lambda: object())
    monkeypatch.setattr(graph, "OpenAIAdversarialProvider", lambda client, model: object())

    class FakeGraph:
        def __init__(self, provider, submit):
            pass

        def run(self, case_id, configuration, mode):
            assert mode == "live_model"
            return graph.AttackOutcome(
                scenario_id=case_id,
                configuration=configuration,
                status="completed",
                termination_reason="completed",
            )

    monkeypatch.setattr(graph, "VibeHackerGraph", FakeGraph)
    response = api_client.post(
        "/api/v1/lab/generated-attack", json={"case_id": "memory_poisoning-00"}
    )
    assert response.status_code == 200
    assert response.json()["victim_model"].startswith("openai:")


def test_generated_attack_persists_real_supervisor_experiment(api_client, monkeypatch):
    from cybotech.agents.contracts import AgentTurn, ModelAction
    from cybotech.api.v1 import security
    from cybotech.services.red_team import graph
    from cybotech.services.red_team.corpus import corpus, corpus_hash
    from cybotech.services.sentinel.service import Sentinel

    class FakeAttack:
        name = "fake:attack-model"

        def __init__(self, client, model):
            pass

        def plan(self, case):
            return graph.AttackStrategy(
                hypothesis="test",
                technique="test",
                payload_type="text/plain",
                expected_outcome="denial",
            )

        def generate_payload(self, case, strategy):
            return graph.LivePayload(
                content=f"SYNTHETIC generated experiment content {case.target}",
                content_type="text/plain",
            )

    class FakePrimary:
        name = "fake:primary-model"

        def generate(self, context):
            return AgentTurn(
                reply="done",
                memories=[],
                actions=[
                    ModelAction(
                        tool_name="read_document", arguments_json='{"id":"memory_poisoning-00"}'
                    )
                ],
                done=True,
            )

    monkeypatch.setenv("CYBOTECH_MODEL_MODE", "live")
    monkeypatch.setattr(security, "runtime_client", lambda: object())
    monkeypatch.setattr(security, "runtime_reviewer", lambda: Sentinel())
    monkeypatch.setattr(security, "runtime_memory_reviewer", lambda: None)
    monkeypatch.setattr(security, "OpenAIProvider", lambda client, model: FakePrimary())
    monkeypatch.setattr(graph, "OpenAIAdversarialProvider", FakeAttack)
    result = api_client.post(
        "/api/v1/lab/generated-attack", json={"case_id": "memory_poisoning-00"}
    )
    assert result.status_code == 200
    assert result.json()["status"] == "completed"
    run_id = result.json()["run_id"]
    stored = api_client.get(f"/api/v1/lab/runs/{run_id}").json()
    original = corpus()[0]
    generated = original.model_copy(
        update={
            "document": original.document.model_copy(
                update={
                    "content": "SYNTHETIC generated experiment content collector-0@attacker.example"
                }
            )
        }
    )
    assert stored["corpus_sha256"] == corpus_hash([generated])
    assert stored["results"][0]["model"] == "fake:primary-model"
    root_events = api_client.get(f"/api/v1/security-events?correlation_id={run_id}").json()["items"]
    root = next(event for event in root_events if event["type"] == "agent_handoff")
    child = next(
        event for event in stored["results"][0]["trace"] if event["type"] == "agent_handoff"
    )
    assert child["payload"]["parent_handoff_id"] == root["payload"]["handoff_id"]


def test_security_events_filter_by_task(api_client):
    for task_id in ("task-one", "task-two"):
        response = api_client.post(
            "/api/v1/assistant/run",
            json={"task": "Summarize", "session_id": "demo", "task_id": task_id},
        )
        assert response.status_code == 200
    events = api_client.get("/api/v1/security-events?correlation_id=task-one").json()
    assert events["items"]
    assert all(event["correlation_id"] == "task-one" for event in events["items"])
    assert events["total"] == len(events["items"])


def test_action_api_denies_unapproved_recipient_and_persists_event(api_client):
    response = api_client.post(
        "/api/v1/actions/authorize",
        json={
            "tool_name": "send_report",
            "arguments": {"destination": "attacker@example.net", "content": "SYNTHETIC"},
            "proposed_by": "primary_assistant",
            "session_id": "s",
            "task_id": "t",
            "user_task_description": "Send the report",
            "retrieved_memory_ids": [],
        },
    )
    assert response.status_code == 200
    assert response.json()["decision"]["authorized"] is False
    events = api_client.get("/api/v1/security-events").json()["items"]
    assert events[0]["type"] == "gateway_decision"
    assert events[0]["outcome"] == "denied"


def test_sensitive_action_requires_and_consumes_exact_approval(api_client):
    payload = {
        "tool_name": "send_report",
        "arguments": {"destination": "reports@internal.example", "content": "synthetic report"},
        "proposed_by": "primary_assistant",
        "session_id": "s",
        "task_id": "t",
        "user_task_description": "Send the weekly report",
        "retrieved_memory_ids": [],
    }
    request = api_client.post("/api/v1/approvals", json=payload)
    assert request.status_code == 200
    approval_id = request.json()["id"]
    decision = api_client.post(
        f"/api/v1/approvals/{approval_id}/decision",
        json={"approved": True, "reviewer": "local_demo_reviewer"},
    )
    assert decision.status_code == 200
    payload["approval_id"] = approval_id
    authorized = api_client.post("/api/v1/actions/authorize", json=payload)
    assert authorized.status_code == 200
    assert authorized.json()["decision"]["authorized"] is True
    payload["arguments"]["content"] = "changed report payload"
    changed = api_client.post("/api/v1/actions/authorize", json=payload)
    assert changed.json()["decision"]["reason"] == "approval_action_mismatch"


def _create_quarantined_memory(api_client: TestClient) -> str:
    response = api_client.post(
        "/api/v1/memories/proposals",
        json={
            "content": "A user supplied fact",
            "category": "support_fact",
            "source_type": "document",
            "source_id": "untrusted-source",
            "source_context": "untrusted content",
        },
    )
    assert response.status_code == 200
    return response.json()["id"]


def test_negative_memory_approval_never_trusts_memory(api_client):
    memory_id = _create_quarantined_memory(api_client)
    response = api_client.post(
        f"/api/v1/memories/{memory_id}/approve",
        json={"approved": False, "reviewer": "local_demo_reviewer"},
    )
    assert response.status_code == 422
    assert api_client.get(f"/api/v1/memories/{memory_id}").json()["status"] == "quarantined"
    history = api_client.get(f"/api/v1/memories/{memory_id}/history").json()["items"]
    assert [event["type"] for event in history] == ["memory_guard_decision"]


def test_memory_approval_requires_valid_body_and_is_not_repeatable(api_client):
    memory_id = _create_quarantined_memory(api_client)
    invalid = api_client.post(f"/api/v1/memories/{memory_id}/approve", json={"approved": True})
    assert invalid.status_code == 422
    assert api_client.get(f"/api/v1/memories/{memory_id}").json()["status"] == "quarantined"

    approved = api_client.post(
        f"/api/v1/memories/{memory_id}/approve",
        json={"approved": True, "reviewer": "local_demo_reviewer"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "trusted"
    repeated = api_client.post(
        f"/api/v1/memories/{memory_id}/approve",
        json={"approved": True, "reviewer": "local_demo_reviewer"},
    )
    assert repeated.status_code == 409


def test_rejection_endpoint_rejects_positive_approval_decision(api_client):
    memory_id = _create_quarantined_memory(api_client)
    response = api_client.post(
        f"/api/v1/memories/{memory_id}/reject",
        json={"approved": True, "reviewer": "local_demo_reviewer"},
    )
    assert response.status_code == 422
    assert api_client.get(f"/api/v1/memories/{memory_id}").json()["status"] == "quarantined"


def test_security_mutations_are_disabled_without_explicit_local_mode(api_client, monkeypatch):
    monkeypatch.delenv("CYBOTECH_SECURITY_MODE")
    response = api_client.post(
        "/api/v1/memories/proposals",
        json={
            "content": "fact",
            "category": "support_fact",
            "source_type": "document",
            "source_id": "source",
        },
    )
    assert response.status_code == 503
    assert api_client.get("/api/v1/memories").status_code == 503


def test_dashboard_aggregates_all_records_and_reports_page_totals(api_client):
    for index in range(3):
        response = api_client.post(
            "/api/v1/memories/proposals",
            json={
                "content": f"Synthetic fact {index}",
                "category": "support_fact",
                "source_type": "document",
                "source_id": f"untrusted-{index}",
            },
        )
        assert response.status_code == 200

    page = api_client.get("/api/v1/memories?limit=1&offset=1").json()
    assert len(page["items"]) == 1
    assert page["total"] == 3
    summary = api_client.get("/api/v1/dashboard/summary").json()
    assert summary["memory_counts"]["quarantined"] == 3
    assert summary["event_total"] == 3


def test_security_mutations_reject_non_loopback_clients(api_client):
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/memories/proposals",
            "query_string": b"",
            "headers": [],
            "client": ("198.51.100.20", 50000),
            "server": ("127.0.0.1", 8000),
            "scheme": "http",
        }
    )
    with pytest.raises(HTTPException) as error:
        require_local_security_access(request)
    assert error.value.status_code == 403


def test_lab_runs_are_isolated_recorded_and_replayable(api_client):
    response = api_client.post(
        "/api/v1/lab/runs",
        json={
            "case_ids": ["memory_poisoning-00"],
            "configurations": ["baseline", "full"],
            "mode": "offline",
        },
    )
    assert response.status_code == 200
    report = response.json()
    assert report["evidence_kind"] == "scripted"
    assert [row["attack_success"] for row in report["results"]] == [True, False]
    assert api_client.get(f"/api/v1/lab/runs/{report['run_id']}").json() == report
    assert api_client.get("/api/v1/memories").json()["total"] == 0


def test_approval_binds_session_and_can_only_be_consumed_once(api_client):
    payload = {
        "tool_name": "send_report",
        "arguments": {"destination": "reports@internal.example", "content": "synthetic report"},
        "proposed_by": "primary_assistant",
        "session_id": "s",
        "task_id": "t",
        "user_task_description": "Send the weekly report",
        "retrieved_memory_ids": [],
    }
    approval_id = api_client.post("/api/v1/approvals", json=payload).json()["id"]
    api_client.post(
        f"/api/v1/approvals/{approval_id}/decision", json={"approved": True, "reviewer": "local"}
    )
    changed = {**payload, "session_id": "different", "approval_id": approval_id}
    assert (
        api_client.post("/api/v1/actions/authorize", json=changed).json()["decision"]["reason"]
        == "approval_action_mismatch"
    )
    first = api_client.post(f"/api/v1/approvals/{approval_id}/execute")
    second = api_client.post(f"/api/v1/approvals/{approval_id}/execute")
    assert first.status_code == 200
    assert first.json()["success"] is True
    assert second.status_code == 409
