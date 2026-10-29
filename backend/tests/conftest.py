"""
Shared pytest fixtures for the Cybotech backend test suite.

Fixtures added here as the implementation progresses:
- Week 1: database session, app client, basic fixtures
- Week 2: attack case fixtures, run state helpers
- Week 3+: memory guard mocks, gateway mocks
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cybotech.main import app


@pytest.fixture(scope="session")
def client() -> TestClient:
    """Return a synchronous test client for the FastAPI app."""
    return TestClient(app)
