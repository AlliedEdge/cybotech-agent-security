"""Readiness checks; no billable model calls occur here."""

import os
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from cybotech import __version__
from cybotech.infrastructure.database.models import Memory
from cybotech.infrastructure.database.session import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check(db: Annotated[Session, Depends(get_db)]) -> JSONResponse:
    dependencies = {
        "database": "unhealthy",
        "vector_db": "unhealthy",
        "llm_provider": "offline"
        if os.getenv("CYBOTECH_MODEL_MODE", "offline") == "offline"
        else "configured_not_probed"
        if os.getenv("OPENAI_API_KEY")
        else "unhealthy",
    }
    try:
        db.execute(select(Memory.id).limit(0))
        dependencies["database"] = "healthy"
    except Exception:
        db.rollback()

    try:
        from cybotech.api.v1.security import runtime_qdrant_store
        dependencies["vector_db"] = runtime_qdrant_store().health()
    except Exception:
        dependencies["vector_db"] = "unhealthy"

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
