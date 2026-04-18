"""Start FastAPI server + trader worker."""
from __future__ import annotations

import os
import sys
import subprocess
import signal
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.config import load_config
from common.db import create_tables
from common.logging import setup_logging, get_logger

log = get_logger(__name__)


def main():
    setup_logging()
    cfg = load_config()
    create_tables()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    log.info("Starting Market AI...")
    log.info("Mode: %s", cfg.mode)

    # Init DB
    subprocess.run([sys.executable, os.path.join(root, "scripts", "init_db.py")], cwd=root)

    # Start FastAPI
    api_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"],
        cwd=root,
    )
    log.info("FastAPI server started (PID %d) at http://localhost:8000", api_proc.pid)

    # Start trader worker
    trader_proc = subprocess.Popen(
        [sys.executable, os.path.join(root, "trader", "main.py")],
        cwd=root,
    )
    log.info("Trader worker started (PID %d)", trader_proc.pid)

    def shutdown(signum, frame):
        log.info("Shutting down...")
        trader_proc.terminate()
        api_proc.terminate()
        trader_proc.wait(timeout=10)
        api_proc.wait(timeout=10)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    log.info("=" * 50)
    log.info("Dashboard: http://localhost:8000")
    log.info("API docs:  http://localhost:8000/docs")
    log.info("Press Ctrl+C to stop.")
    log.info("=" * 50)

    try:
        while True:
            if api_proc.poll() is not None:
                log.error("FastAPI exited unexpectedly!")
                break
            if trader_proc.poll() is not None:
                log.error("Trader exited unexpectedly!")
                break
            time.sleep(2)
    except KeyboardInterrupt:
        shutdown(None, None)


if __name__ == "__main__":
    main()
