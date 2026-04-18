"""FastAPI dependencies."""
from __future__ import annotations

from typing import Generator

from sqlalchemy.orm import Session

from common.db import get_session_factory


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
