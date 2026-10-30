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
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from cybotech.infrastructure.database.models import Base
from cybotech.infrastructure.database.session import get_db
from cybotech.main import app


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)

    def override_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    engine.dispose()
