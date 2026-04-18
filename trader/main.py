"""Trader worker entry point."""
from __future__ import annotations

import sys
import os
import signal

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.config import load_config
from common.db import create_tables
from common.logging import get_logger, setup_logging
from trader.ibkr_client import get_ibkr_client
from trader.universe import seed_universe
from trader.scheduler import Scheduler
from trader.risk import log_event

log = get_logger(__name__)


def main():
    setup_logging()
    cfg = load_config()
    create_tables()

    log.info("=" * 60)
    log.info("Market AI Trader — mode=%s", cfg.mode)
    log.info("IBKR: %s:%s  clientId=%s", cfg.ibkr.host, cfg.ibkr.port, cfg.ibkr.client_id)
    log.info("=" * 60)

    if cfg.mode == "LIVE":
        log.warning("LIVE MODE — real money at risk!")

    # Seed universe
    seed_universe()

    # Connect to IBKR
    client = get_ibkr_client()
    try:
        client.connect()
    except Exception as e:
        log.error("Failed to connect to IBKR: %s", e)
        log.error("Make sure TWS or IB Gateway is running on %s:%s", cfg.ibkr.host, cfg.ibkr.port)
        log.info("Starting in offline mode — no trading, sentiment-only updates.")
        client = None

    log_event("INFO", "startup", f"Trader started in {cfg.mode} mode")

    # Start scheduler
    scheduler = Scheduler(client)

    def shutdown(signum, frame):
        log.info("Shutdown signal received.")
        scheduler.stop()
        if client:
            client.disconnect()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        scheduler.run()
    except KeyboardInterrupt:
        scheduler.stop()
        if client:
            client.disconnect()


if __name__ == "__main__":
    main()
