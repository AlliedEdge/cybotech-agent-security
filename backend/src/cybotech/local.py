"""Start a loopback-only demo: python -m cybotech.local.

SQLite bootstrap is explicitly for the local demo. PostgreSQL uses Alembic migrations.
Qdrant runs as an embedded in-memory instance — no Docker or QDRANT_URL needed.
"""

import os
from pathlib import Path

import uvicorn

from cybotech.infrastructure.database.models import Base
from cybotech.infrastructure.database.session import get_engine
from cybotech.infrastructure.vector.qdrant import make_in_memory_store


def main() -> None:
    if "DATABASE_URL" not in os.environ:
        path = Path(".local/cybotech.db").resolve()
        path.parent.mkdir(exist_ok=True)
        os.environ["DATABASE_URL"] = f"sqlite:///{path.as_posix()}"

    os.environ.setdefault("CYBOTECH_SECURITY_MODE", "local_demo")
    os.environ.setdefault("CYBOTECH_MODEL_MODE", "offline")

    # Bootstrap SQLite schema (PostgreSQL uses Alembic migrations instead).
    engine = get_engine()
    if engine.dialect.name == "sqlite":
        Base.metadata.create_all(engine)

    # Pre-create the in-memory Qdrant store and register it as the
    # module-level singleton before the FastAPI app starts handling requests.
    # This ensures the same QdrantClient instance is reused across all requests.
    store = make_in_memory_store()
    from cybotech.api.v1.security import set_qdrant_store
    set_qdrant_store(store)

    uvicorn.run("cybotech.main:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
