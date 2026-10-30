"""
Cybotech FastAPI application factory.

This module creates the FastAPI app instance, registers routers,
configures middleware, and exposes the health endpoint.

Includes assistant orchestration, memory review, approvals, attack experiments, and audit replay.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
    from cybotech.api.v1.security import router as security_router

    application.include_router(security_router, prefix="/api/v1")


# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

app = create_app()
