"""Initialize the database via Alembic migrations and seed bot_state."""
import sys
import os
from pathlib import Path

# Ensure project root on sys.path.
_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_root))

from alembic import command
from alembic.config import Config

from common.config import load_config
from common.db import get_db
from common.models import BotState


def run_migrations() -> None:
    alembic_cfg = Config(str(_root / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(_root / "alembic"))
    command.upgrade(alembic_cfg, "head")


def seed() -> None:
    cfg = load_config()
    with get_db() as db:
        state = db.query(BotState).first()
        if state is None:
            state = BotState(
                id=1,
                paused=False,
                kill_switch=False,
                options_enabled=cfg.options.enabled,
                approve_mode=cfg.features.approve_mode_default,
            )
            db.add(state)
            print("Seeded bot_state row.")
        else:
            print("bot_state already exists, skipping seed.")


def main():
    cfg = load_config()
    db_url = os.environ.get("DATABASE_URL") or cfg.db.url or f"sqlite:///{cfg.db.path}"
    print(f"Running migrations against: {db_url} ...")
    run_migrations()
    print("Migrations applied. Seeding ...")
    seed()
    print("Done.")


if __name__ == "__main__":
    main()
