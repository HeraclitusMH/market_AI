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
    _auto_seed_security_master()


def _auto_seed_security_master() -> None:
    """Import us_listed_master.csv + manual overrides on first startup if the
    security_master table is empty.  Idempotent: skips when rows already exist."""
    try:
        from common.models import SecurityMaster
        with get_db() as db:
            if db.query(SecurityMaster.symbol).limit(1).first() is not None:
                return  # already seeded
        # Table is empty — import now
        from common.config import get_config
        from trader.securities.master import import_csv, load_manual_overrides
        import logging
        _log = logging.getLogger(__name__)
        cfg = get_config()
        csv_path = cfg.securities.master_csv_path
        _log.info("Security master is empty — auto-seeding from %s", csv_path)
        summary = import_csv(csv_path, verify_ibkr=False, refresh_aliases=True)
        _log.info(
            "Security master auto-seed done: added=%d aliases=%d",
            summary["added"], summary["aliases_written"],
        )
        ov = load_manual_overrides(cfg.securities.alias_overrides_path)
        _log.info("Manual alias overrides loaded: %d", ov["loaded"])
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "Security master auto-seed failed (non-fatal): %s", exc
        )


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
