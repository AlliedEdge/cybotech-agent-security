"""
Alembic environment configuration.

Reads database connection details from environment variables.
Never hardcodes credentials.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# ---------------------------------------------------------------------------
# Alembic Config object
# ---------------------------------------------------------------------------
config = context.config

# Set up Python logging from the alembic.ini [loggers] section
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Target metadata for autogenerate support
# Import Base from the domain models once they are implemented.
# Placeholder: set to None until models exist.
# ---------------------------------------------------------------------------
# from cybotech.infrastructure.database.base import Base
# target_metadata = Base.metadata
target_metadata = None  # replace with Base.metadata in Week 1


# ---------------------------------------------------------------------------
# Database URL — assembled from environment variables
# ---------------------------------------------------------------------------
def get_database_url() -> str:
    """Build the database URL from environment variables."""
    host     = os.environ.get("POSTGRES_HOST", "localhost")
    port     = os.environ.get("POSTGRES_PORT", "5432")
    db       = os.environ.get("POSTGRES_DB", "cybotech")
    user     = os.environ.get("POSTGRES_USER", "cybotech_user")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"


# ---------------------------------------------------------------------------
# Run migrations
# ---------------------------------------------------------------------------
def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no live DB connection required)."""
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (live DB connection)."""
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
