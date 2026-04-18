#!/usr/bin/env bash
# Entrypoint for the trader container.
# Waits for Postgres, then runs the trader worker. Does NOT run migrations
# (the api container is the single migration runner). Failure to reach
# IB Gateway is handled inside trader/main.py — the worker logs and
# continues in offline mode, so the container stays up.
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL must be set for the trader container}"

echo "[entrypoint-trader] Waiting for Postgres..."
python - <<'PY'
import os
import sys
import time
from sqlalchemy import create_engine, text

url = os.environ["DATABASE_URL"]
deadline = time.time() + 90
last_err = None
while time.time() < deadline:
    try:
        eng = create_engine(url, pool_pre_ping=True)
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
        print("[entrypoint-trader] Postgres is ready.")
        sys.exit(0)
    except Exception as e:
        last_err = e
        time.sleep(1)
print(f"[entrypoint-trader] Postgres did not become ready in time: {last_err}", file=sys.stderr)
sys.exit(1)
PY

echo "[entrypoint-trader] Launching trader worker."
exec python trader/main.py
