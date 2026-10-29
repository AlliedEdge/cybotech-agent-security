# =============================================================================
# Cybotech Backend — Dockerfile
# Multi-stage build using uv for dependency management.
# Python 3.12, FastAPI, Uvicorn.
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: dependency resolver
# Installs only production dependencies into a clean prefix.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS deps

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency manifests only — layer cached until pyproject.toml changes
COPY pyproject.toml ./

# Install dependencies into /app/.venv (no dev/test extras)
RUN uv sync --no-dev --frozen

# ---------------------------------------------------------------------------
# Stage 2: runtime image
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# Security: run as non-root user
RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --no-create-home appuser

WORKDIR /app

# Copy the resolved virtualenv from the deps stage
COPY --from=deps /app/.venv /app/.venv

# Copy application source
COPY src/ ./src/

# Make uv-installed binaries available
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Drop privileges
USER appuser

EXPOSE 8000

# Health check (requires curl in the base image; use wget if unavailable)
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" || exit 1

# Start Uvicorn
# For production: remove --reload and set --workers to match CPU count
CMD ["uvicorn", "cybotech.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
