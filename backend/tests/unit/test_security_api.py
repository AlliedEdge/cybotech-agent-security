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
