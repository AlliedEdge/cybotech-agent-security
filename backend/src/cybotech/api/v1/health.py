"""
Health endpoint — GET /api/v1/health

Returns service status and dependency health.
This is the only implemented endpoint in the foundation scaffold.
All other endpoints are planned for Week 1+ implementation.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> JSONResponse:
    """
    Return service health and dependency status.

    Checks:
    - Application is running
    - Database connectivity (planned — returns 'unknown' until DB layer is wired)
    - pgvector extension (planned)
    - LLM provider reachability (planned)
    """
    # NOTE: Real dependency checks are planned for Week 1.
    # Currently returns a minimal healthy response to verify the app starts.
    dependencies: dict[str, str] = {
        "database": "unknown",   # will be 'healthy'/'unhealthy' once DB layer is wired
        "pgvector": "unknown",
        "llm_provider": "unknown",
    }

    all_healthy = all(v != "unhealthy" for v in dependencies.values())
    overall_status = "healthy" if all_healthy else "degraded"

    status_code = 200 if overall_status == "healthy" else 503

    return JSONResponse(
        status_code=status_code,
        content={
            "data": {
                "status": overall_status,
                "version": "0.1.0",
                "dependencies": dependencies,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            "meta": {
                "request_id": None,  # will be set by request-id middleware (planned)
            },
        },
    )
