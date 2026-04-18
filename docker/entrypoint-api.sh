#!/usr/bin/env bash
# Entrypoint for the API container.
# 1. Wait for Postgres to accept connections.
# 2. Run alembic migrations (idempotent — no-op when at head).
# 3. Seed bot_state row if missing.
# 4. Exec uvicorn.
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL must be set for the api container}"

echo "[entrypoint-api] Waiting for Postgres..."
python - <<'PY'
import os
import sys
import time
from sqlalchemy import create_engine, text

url = os.environ["DATABASE_URL"]
deadline = time.time() + 60
last_err = None
while time.time() < deadline:
    try:
        eng = create_engine(url, pool_pre_ping=True)
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
        print("[entrypoint-api] Postgres is ready.")
        sys.exit(0)
    except Exception as e:
        last_err = e
        time.sleep(1)
print(f"[entrypoint-api] Postgres did not become ready in time: {last_err}", file=sys.stderr)
sys.exit(1)
PY

echo "[entrypoint-api] Running migrations + seed via scripts/init_db.py..."
# init_db.py calls alembic upgrade head (idempotent) then seeds bot_state.
python scripts/init_db.py

echo "[entrypoint-api] Launching uvicorn."
exec uvicorn api.main:app --host 0.0.0.0 --port 8000
