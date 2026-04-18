from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make sure the project root is on sys.path so common.* is importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.models import Base  # noqa: E402  (after sys.path patch)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_url() -> str:
    """Resolve DB URL: env var → common.config → fallback."""
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        return env_url
    try:
        from common.config import get_config
        cfg = get_config()
        if cfg.db.url:
            return cfg.db.url
        return f"sqlite:///{cfg.db.path}"
    except Exception:
        raise RuntimeError(
            "Set DATABASE_URL env var or configure db.url in config.yaml"
        )


def run_migrations_offline() -> None:
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _get_url()
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = url
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
