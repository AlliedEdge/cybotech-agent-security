"""Start a loopback-only demo: python -m cybotech.local.

SQLite bootstrap is explicitly for the local demo. PostgreSQL uses Alembic migrations.
"""

import os
from pathlib import Path

import uvicorn

from cybotech.infrastructure.database.models import Base
from cybotech.infrastructure.database.session import get_engine


def main() -> None:
    if "DATABASE_URL" not in os.environ:
        path = Path(".local/cybotech.db").resolve()
        path.parent.mkdir(exist_ok=True)
        os.environ["DATABASE_URL"] = f"sqlite:///{path.as_posix()}"
    os.environ.setdefault("CYBOTECH_SECURITY_MODE", "local_demo")
    os.environ.setdefault("CYBOTECH_MODEL_MODE", "offline")
    engine = get_engine()
    if engine.dialect.name == "sqlite":
        Base.metadata.create_all(engine)
    uvicorn.run("cybotech.main:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
