"""
Unit tests for the health endpoint.

These tests verify the health endpoint is reachable and returns
the expected structure. They do not require a live database.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_health_returns_200(client: TestClient) -> None:
    """Health endpoint should return 200 when no dependencies are unhealthy."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200


def test_health_response_structure(client: TestClient) -> None:
    """Health response must include data.status and data.dependencies."""
    response = client.get("/api/v1/health")
    body = response.json()

    assert "data" in body
    data = body["data"]

    assert "status" in data
    assert data["status"] in ("healthy", "degraded")

    assert "version" in data
    assert "dependencies" in data

    deps = data["dependencies"]
    assert "database" in deps
    assert "pgvector" in deps
    assert "llm_provider" in deps


def test_health_version_matches_package(client: TestClient) -> None:
    """Health endpoint version should match the package __version__."""
    from cybotech import __version__

    response = client.get("/api/v1/health")
    assert response.json()["data"]["version"] == __version__
