"""Database engine and session helpers."""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from common.config import get_config
from common.models import Base

_engine = None
_SessionLocal = None


def _build_url() -> str:
    # Priority: DATABASE_URL env var → config db.url → sqlite fallback via db.path
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        return env_url
    cfg = get_config()
    if cfg.db.url:
        return cfg.db.url
    return f"sqlite:///{cfg.db.path}"


def get_engine():
    global _engine
    if _engine is None:
        url = _build_url()
        if url.startswith("postgresql"):
            _engine = create_engine(
                url,
                echo=False,
                pool_pre_ping=True,   # detect stale connections before use
                pool_size=5,
                max_overflow=10,
                pool_timeout=30,
            )
        else:
            _engine = create_engine(
                url,
                echo=False,
                connect_args={"check_same_thread": False},
            )
    return _engine


def get_session_factory() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


def create_tables() -> None:
    """Create all tables directly — used for SQLite dev/test only.
    For Postgres, prefer running `alembic upgrade head`."""
    Base.metadata.create_all(get_engine())


@contextmanager
def get_db() -> Generator[Session, None, None]:
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
