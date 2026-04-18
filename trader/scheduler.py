"""Scheduler: heartbeat, sentiment refresh, signal eval, rebalance, sync."""
from __future__ import annotations

import time
import threading
from datetime import datetime, timedelta
from typing import Optional

from common.config import get_config
from common.db import get_db
from common.logging import get_logger
from common.models import BotState
from common.time import utcnow

log = get_logger(__name__)


class Scheduler:
    """Simple time-based scheduler for the trading loop."""

    def __init__(self, client=None):
        self.cfg = get_config()
        self.client = client
        self._stop = threading.Event()

        self._last_sentiment = datetime.min
        self._last_signal = datetime.min
        self._last_rebalance: Optional[str] = None  # date string
        self._last_sync = datetime.min

    def _heartbeat(self) -> None:
        with get_db() as db:
            state = db.query(BotState).first()
            if state:
                state.last_heartbeat = utcnow()

    def _should_refresh_sentiment(self) -> bool:
        # Prefer the sentiment-section interval (spec: 60 minutes); fall back to
        # the legacy scheduling-section value for back-compat with old configs.
        interval = getattr(self.cfg.sentiment, "refresh_minutes", None) \
            or self.cfg.scheduling.sentiment_refresh_minutes
        return (datetime.now() - self._last_sentiment).total_seconds() > interval * 60

    def _should_eval_signals(self) -> bool:
        interval = self.cfg.scheduling.signal_eval_minutes
        return (datetime.now() - self._last_signal).total_seconds() > interval * 60

    def _should_rebalance(self) -> bool:
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        if self._last_rebalance == today_str:
            return False
        rebalance_time = self.cfg.scheduling.rebalance_time_local
        try:
            h, m = map(int, rebalance_time.split(":"))
            target = now.replace(hour=h, minute=m, second=0, microsecond=0)
            return now >= target
        except Exception:
            return False

    def _should_sync(self) -> bool:
        return (datetime.now() - self._last_sync).total_seconds() > 30

    def run_once(self) -> None:
        """Single iteration of the scheduler loop."""
        from trader.sync import full_sync
        from trader.sentiment.factory import refresh_and_store
        from trader.strategy import generate_signals
        from trader.execution import execute_signal

        self._heartbeat()

        # Sync with IBKR
        if self._should_sync():
            try:
                full_sync(self.client)
                self._last_sync = datetime.now()
            except Exception as e:
                log.error("Sync failed: %s", e)

        # Refresh sentiment
        if self._should_refresh_sentiment():
            try:
                summary = refresh_and_store()
                self._last_sentiment = datetime.now()
                log.info(
                    "Sentiment refreshed: provider=%s status=%s snapshots=%s",
                    summary.get("provider"), summary.get("status"),
                    summary.get("snapshots_written", 0),
                )
            except Exception as e:
                log.error("Sentiment refresh failed: %s", e)

        # Evaluate signals
        if self._should_eval_signals():
            try:
                signals = generate_signals(self.client)
                self._last_signal = datetime.now()
                log.info("Signal evaluation: %d signals generated.", len(signals))
            except Exception as e:
                log.error("Signal evaluation failed: %s", e)

        # Rebalance: execute top signals once per day
        if self._should_rebalance():
            try:
                log.info("Rebalance triggered.")
                signals = generate_signals(self.client)
                for intent in signals:
                    try:
                        execute_signal(intent, self.client)
                    except Exception as e:
                        log.error("Execution failed for %s: %s", intent.symbol, e)
                self._last_rebalance = datetime.now().strftime("%Y-%m-%d")
            except Exception as e:
                log.error("Rebalance failed: %s", e)

    def run(self) -> None:
        """Main loop — runs until stopped."""
        log.info("Scheduler started.")
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as e:
                log.error("Scheduler iteration error: %s", e)
            self._stop.wait(10)  # heartbeat interval
        log.info("Scheduler stopped.")

    def stop(self) -> None:
        self._stop.set()
