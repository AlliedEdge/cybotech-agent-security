"""Readiness checks; no billable model calls occur here."""

import os
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from cybotech import __version__
from cybotech.agents.providers import provider_settings
from cybotech.agents.registry import AgentId
from cybotech.infrastructure.database.models import Memory
from cybotech.infrastructure.database.session import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check(db: Annotated[Session, Depends(get_db)]) -> JSONResponse:
    provider_status = "offline"
    if os.getenv("CYBOTECH_MODEL_MODE", "offline") != "offline":
        try:
            provider_settings(AgentId.PRIMARY)
            provider_status = "configured_not_probed"
        except ValueError:
            provider_status = "unhealthy"
    dependencies = {
        "database": "unhealthy",
        "pgvector": "not_applicable",
        "llm_provider": provider_status,
    }
    try:
        db.execute(select(Memory.id).limit(0))
        dependencies["database"] = "healthy"
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            installed = db.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname='vector'")
            )
            dependencies["pgvector"] = "healthy" if installed else "unhealthy"
    except Exception:
        db.rollback()
    healthy = "unhealthy" not in dependencies.values()
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "data": {
                "status": "healthy" if healthy else "degraded",
                "version": __version__,
                "dependencies": dependencies,
                "timestamp": datetime.now(UTC).isoformat(),
            },
            "meta": {"request_id": None},
        },
    )
