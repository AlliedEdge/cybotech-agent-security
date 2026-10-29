"""
Cybotech FastAPI application factory.

This module creates the FastAPI app instance, registers routers,
configures middleware, and exposes the health endpoint.

Status: foundation scaffold — only the health endpoint is implemented.
All other routers are placeholders for Week 1+ implementation.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    application = FastAPI(
        title="Cybotech",
        description="Agent Memory Security & Red-Team Platform",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    # CORS — restrict in production; permissive for local dev
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routers
    _register_routers(application)

    return application


def _register_routers(application: FastAPI) -> None:
    """Register all API routers. Placeholders added as routes are implemented."""
    from cybotech.api.v1.health import router as health_router

    application.include_router(health_router, prefix="/api/v1")

    # Planned routers — uncomment as each is implemented:
    # from cybotech.api.v1.routers.sessions import router as sessions_router
    # from cybotech.api.v1.routers.documents import router as documents_router
    # from cybotech.api.v1.routers.memories import router as memories_router
    # from cybotech.api.v1.routers.security_events import router as events_router
    # from cybotech.api.v1.routers.attack_runs import router as attack_runs_router
    # from cybotech.api.v1.routers.evaluations import router as evaluations_router
    # from cybotech.api.v1.routers.tool_calls import router as tool_calls_router
    # from cybotech.api.v1.routers.policies import router as policies_router
    # from cybotech.api.v1.routers.approvals import router as approvals_router
    # from cybotech.api.v1.routers.sse import router as sse_router


# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

app = create_app()
