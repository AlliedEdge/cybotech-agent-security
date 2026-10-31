"""Unit tests for evaluation API endpoints.

Covers: filters, pagination, empty results, invalid requests,
local-demo security restrictions, no-mutation guarantee, and POST /run.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from cybotech.infrastructure.database.models import AuditEvent, Base
from cybotech.infrastructure.database.session import get_db
from cybotech.main import app

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


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


def _seed_run(db: Session, *, run_id: str = "run_test_001", cases: int = 2) -> None:
    """Insert synthetic lab_run_completed and lab_case_completed AuditEvent rows."""
    results = []
    for i in range(cases):
        case_payload = {
            "run_id": run_id,
            "case_id": f"memory_poisoning-{i:02d}",
            "kind": "attack",
            "objective": "memory_poisoning",
            "split": "development",
            "configuration": "full",
            "defense_profile": {
                "name": "full",
                "memory_guard": True,
                "sentinel": True,
                "gateway": True,
                "sandbox": "immutable_simulated",
            },
            "model": "scripted-adversary-v2",
            "status": "completed",
            "errors": [],
            "attack_success": False,
            "poisoned_memory": False,
            "attacker_delivery": False,
            "legitimate_task_success": None,
            "memory_states": ["quarantined"],
            "latency_ms": 200.0 + i * 10,
            "trace": [],
        }
        db.add(
            AuditEvent(
                event_id=f"evt_case_{run_id}_{i}",
                event_type="lab_case_completed",
                actor="lab",
                correlation_id=run_id,
                outcome="completed",
                payload=case_payload,
            )
        )
        results.append(case_payload)

    run_payload = {
        "run_id": run_id,
        "corpus_version": "synthetic-v1",
        "corpus_sha256": "abcdef12" * 8,
        "evidence_kind": "scripted",
        "storage": "isolated_sqlite",
        "embedding": "local-token-hash-384-v1",
        "reviewer": "deterministic",
        "tools": "simulated",
        "metrics": [],
        "results": results,
    }
    db.add(
        AuditEvent(
            event_id=f"evt_run_{run_id}",
            event_type="lab_run_completed",
            actor="lab",
            correlation_id=run_id,
            outcome="completed",
            payload=run_payload,
        )
    )
    db.commit()


@pytest.fixture
def seeded_client(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """API client with pre-seeded experiment data."""
    # We need to seed using the same overridden DB.
    # Reach into the override to get a session.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)

    def override_db():
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    # Seed the DB
    with Session(engine, expire_on_commit=False) as db:
        _seed_run(db, run_id="run_001", cases=3)
        _seed_run(db, run_id="run_002", cases=2)

    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    engine.dispose()


# ---------------------------------------------------------------------------
# Security restrictions
# ---------------------------------------------------------------------------


class TestSecurityRestrictions:
    def test_evaluation_disabled_without_local_demo_mode(
        self, api_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("CYBOTECH_SECURITY_MODE")
        response = api_client.get("/api/v1/evaluations/summary")
        assert response.status_code == 503

    def test_evaluation_disabled_with_wrong_mode(
        self, api_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("CYBOTECH_SECURITY_MODE", "production")
        response = api_client.get("/api/v1/evaluations/summary")
        assert response.status_code == 503

    def test_non_loopback_client_rejected(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("CYBOTECH_SECURITY_MODE", "local_demo")
        from fastapi import HTTPException
        from starlette.requests import Request

        from cybotech.api.v1.security import require_local_security_access

        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/evaluations/summary",
                "query_string": b"",
                "headers": [],
                "client": ("198.51.100.1", 12345),
                "server": ("127.0.0.1", 8000),
                "scheme": "http",
            }
        )
        with pytest.raises(HTTPException) as exc:
            require_local_security_access(request)
        assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Empty result sets
# ---------------------------------------------------------------------------


class TestEmptyResults:
    def test_summary_with_no_data(self, api_client: TestClient):
        response = api_client.get("/api/v1/evaluations/summary")
        assert response.status_code == 200
        body = response.json()
        assert body["total_experiment_records"] == 0
        assert body["strata"][0]["sample_count"] == 0

    def test_experiments_empty(self, api_client: TestClient):
        response = api_client.get("/api/v1/evaluations/experiments")
        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_runs_empty(self, api_client: TestClient):
        response = api_client.get("/api/v1/evaluations/runs")
        assert response.status_code == 200
        assert response.json()["items"] == []

    def test_metrics_empty(self, api_client: TestClient):
        response = api_client.get("/api/v1/evaluations/metrics")
        assert response.status_code == 200
        body = response.json()
        assert body["total_experiment_records"] == 0


# ---------------------------------------------------------------------------
# Valid requests with seeded data
# ---------------------------------------------------------------------------


class TestWithSeededData:
    def test_summary_returns_real_counts(self, seeded_client: TestClient):
        response = seeded_client.get("/api/v1/evaluations/summary")
        assert response.status_code == 200
        body = response.json()
        # 3 + 2 cases seeded
        assert body["total_experiment_records"] == 5
        assert body["valid_attack_records"] == 5
        assert body["failed_records"] == 0

    def test_summary_all_attacks_blocked(self, seeded_client: TestClient):
        response = seeded_client.get("/api/v1/evaluations/summary")
        body = response.json()
        overall_stratum = next(s for s in body["strata"] if s["population_label"] == "all")
        asr = overall_stratum["attack_success_rate"]
        assert asr["available"] is True
        assert asr["numerator"] == 0
        assert asr["denominator"] == 5
        assert asr["percent"] == 0.0

    def test_experiments_lists_all_records(self, seeded_client: TestClient):
        response = seeded_client.get("/api/v1/evaluations/experiments")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 5
        assert len(body["items"]) == 5

    def test_experiments_pagination(self, seeded_client: TestClient):
        r1 = seeded_client.get("/api/v1/evaluations/experiments?limit=2&offset=0")
        r2 = seeded_client.get("/api/v1/evaluations/experiments?limit=2&offset=2")
        assert r1.status_code == r2.status_code == 200
        assert len(r1.json()["items"]) == 2
        assert len(r2.json()["items"]) == 2
        # Total is always 5 regardless of page
        assert r1.json()["total"] == r2.json()["total"] == 5
        # Items on different pages are positionally distinct (they come from different offsets)
        # Use (run_id, case_id) tuples to distinguish across runs
        keys1 = {(i["run_id"], i["case_id"]) for i in r1.json()["items"]}
        keys2 = {(i["run_id"], i["case_id"]) for i in r2.json()["items"]}
        assert keys1.isdisjoint(keys2)

    def test_experiments_filter_by_run_id(self, seeded_client: TestClient):
        response = seeded_client.get("/api/v1/evaluations/experiments?run_id=run_001")
        body = response.json()
        assert body["total"] == 3
        assert all(i["run_id"] == "run_001" for i in body["items"])

    def test_runs_list(self, seeded_client: TestClient):
        response = seeded_client.get("/api/v1/evaluations/runs")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        run_ids = {r["run_id"] for r in body["items"]}
        assert run_ids == {"run_001", "run_002"}

    def test_get_experiment_by_id(self, seeded_client: TestClient):
        response = seeded_client.get("/api/v1/evaluations/experiments/run_001/memory_poisoning-00")
        assert response.status_code == 200
        body = response.json()
        assert body["case_id"] == "memory_poisoning-00"
        assert body["run_id"] == "run_001"
        assert body["outcome"] == "attack_blocked"

    def test_get_experiment_not_found(self, seeded_client: TestClient):
        response = seeded_client.get("/api/v1/evaluations/experiments/run_999/nonexistent")
        assert response.status_code == 404

    def test_metrics_endpoint_lists_all_metric_names(self, seeded_client: TestClient):
        response = seeded_client.get("/api/v1/evaluations/metrics")
        assert response.status_code == 200
        items = response.json()["items"]
        metric_names = {i["metric_name"] for i in items}
        expected = {
            "attack_success_rate",
            "memory_poisoning_success_rate",
            "unauthorized_tool_execution_rate",
            "detection_rate",
            "false_positive_rate",
            "latency_overhead",
        }
        assert expected.issubset(metric_names)

    def test_comparability_endpoint_with_no_baseline_data(self, seeded_client: TestClient):
        # All seeded data is "full" configuration — no baseline
        response = seeded_client.get("/api/v1/evaluations/comparability")
        assert response.status_code == 200
        body = response.json()
        assert body["comparability"]["comparable"] is False
        assert body["evaluation"] is None
        assert "note" in body


# ---------------------------------------------------------------------------
# Filter validation
# ---------------------------------------------------------------------------


class TestFilterValidation:
    def test_invalid_generation_mode_rejected(self, api_client: TestClient):
        response = api_client.get("/api/v1/evaluations/summary?generation_mode=unknown")
        assert response.status_code == 422

    def test_invalid_configuration_rejected(self, api_client: TestClient):
        response = api_client.get("/api/v1/evaluations/summary?configuration=bad")
        assert response.status_code == 422

    def test_invalid_scenario_kind_rejected(self, api_client: TestClient):
        response = api_client.get("/api/v1/evaluations/summary?scenario_kind=malicious")
        assert response.status_code == 422

    def test_invalid_scenario_objective_rejected(self, api_client: TestClient):
        response = api_client.get("/api/v1/evaluations/summary?scenario_objective=hack")
        assert response.status_code == 422

    def test_invalid_outcome_filter_rejected(self, api_client: TestClient):
        response = api_client.get("/api/v1/evaluations/experiments?outcome=invalid_value")
        assert response.status_code == 422

    def test_valid_outcome_filter_accepted(self, api_client: TestClient):
        response = api_client.get("/api/v1/evaluations/experiments?outcome=attack_blocked")
        assert response.status_code == 200

    def test_outcome_filter_is_applied_before_pagination(self, seeded_client: TestClient):
        response = seeded_client.get(
            "/api/v1/evaluations/experiments?outcome=attack_blocked&limit=2&offset=2"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 5
        assert len(body["items"]) == 2
        assert all(item["outcome"] == "attack_blocked" for item in body["items"])

    def test_limit_too_large_rejected(self, api_client: TestClient):
        response = api_client.get("/api/v1/evaluations/experiments?limit=9999")
        assert response.status_code == 422

    def test_negative_offset_rejected(self, api_client: TestClient):
        response = api_client.get("/api/v1/evaluations/experiments?offset=-1")
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /evaluations/run
# ---------------------------------------------------------------------------


class TestPostEvaluationsRun:
    def test_run_executes_and_returns_evaluation(self, api_client: TestClient):
        response = api_client.post(
            "/api/v1/evaluations/run",
            json={
                "case_ids": ["memory_poisoning-00"],
                "configurations": ["full"],
                "mode": "offline",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert "run_id" in body
        assert body["case_count"] == 1
        assert body["configuration_count"] == 1
        assert "evaluation" in body
        assert body["evaluation"]["total_experiment_records"] == 1

    def test_run_with_baseline_and_full(self, api_client: TestClient):
        response = api_client.post(
            "/api/v1/evaluations/run",
            json={
                "case_ids": ["memory_poisoning-00", "unauthorized_disclosure-00"],
                "configurations": ["baseline", "full"],
                "mode": "offline",
            },
        )
        assert response.status_code == 200
        body = response.json()
        # 2 cases x 2 configs = 4 experiments
        assert body["evaluation"]["total_experiment_records"] == 4

    def test_run_rejects_unknown_case_ids(self, api_client: TestClient):
        response = api_client.post(
            "/api/v1/evaluations/run",
            json={
                "case_ids": ["nonexistent-case-99"],
                "configurations": ["full"],
                "mode": "offline",
            },
        )
        assert response.status_code == 422

    def test_run_rejects_duplicate_case_ids(self, api_client: TestClient):
        response = api_client.post(
            "/api/v1/evaluations/run",
            json={
                "case_ids": ["memory_poisoning-00", "memory_poisoning-00"],
                "configurations": ["full"],
                "mode": "offline",
            },
        )
        assert response.status_code == 422

    def test_run_rejects_duplicate_configurations(self, api_client: TestClient):
        response = api_client.post(
            "/api/v1/evaluations/run",
            json={
                "case_ids": ["memory_poisoning-00"],
                "configurations": ["full", "full"],
                "mode": "offline",
            },
        )
        assert response.status_code == 422

    def test_live_mode_requires_env_var(
        self, api_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("CYBOTECH_MODEL_MODE", "offline")
        response = api_client.post(
            "/api/v1/evaluations/run",
            json={
                "case_ids": ["memory_poisoning-00"],
                "configurations": ["full"],
                "mode": "live",
            },
        )
        assert response.status_code == 422
        assert "CYBOTECH_MODEL_MODE=live" in response.json()["detail"]

    def test_run_does_not_mutate_existing_experiment_outcomes(self, seeded_client: TestClient):
        """Running a new experiment must not alter previously persisted records."""
        # Get initial counts
        before = seeded_client.get("/api/v1/evaluations/experiments").json()["total"]

        seeded_client.post(
            "/api/v1/evaluations/run",
            json={
                "case_ids": ["memory_poisoning-00"],
                "configurations": ["full"],
                "mode": "offline",
            },
        )

        after = seeded_client.get("/api/v1/evaluations/experiments").json()["total"]
        # One new experiment added — previously seeded experiments intact
        assert after == before + 1


# ---------------------------------------------------------------------------
# Comparability endpoint
# ---------------------------------------------------------------------------


class TestComparabilityEndpoint:
    def test_invalid_generation_mode_rejected(self, api_client: TestClient):
        response = api_client.get("/api/v1/evaluations/comparability?generation_mode=bad")
        assert response.status_code == 422

    def test_empty_data_not_comparable(self, api_client: TestClient):
        response = api_client.get("/api/v1/evaluations/comparability")
        assert response.status_code == 200
        body = response.json()
        assert body["comparability"]["comparable"] is False
