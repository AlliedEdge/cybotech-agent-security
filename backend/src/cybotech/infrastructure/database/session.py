import os
from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session, sessionmaker


def database_url() -> str:
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "cybotech")
    user = os.getenv("POSTGRES_USER", "cybotech_user")
    password = os.getenv("POSTGRES_PASSWORD", "")
    return URL.create(
        "postgresql+psycopg",
        username=user,
        password=password,
        host=host,
        port=int(port),
        database=db,
    ).render_as_string(hide_password=False)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    url = database_url()
    return create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5} if url.startswith("postgresql") else {},
    )


def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, class_=Session)


def get_db() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
