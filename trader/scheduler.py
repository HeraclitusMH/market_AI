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


def _pricing_field(plan, key: str, default: float = 0.0) -> float:
    import json
    try:
        return json.loads(plan.pricing_json or "{}").get(key, default)
    except Exception:
        return default


class Scheduler:
    """Simple time-based scheduler for the trading loop."""

    def __init__(self, client=None):
        self.cfg = get_config()
        self.client = client
        self._stop = threading.Event()

        self._last_sentiment = datetime.min
        self._last_signal = datetime.min
        self._last_ranking = datetime.min
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

    def _should_rank(self) -> bool:
        # Run ranking after each sentiment refresh (same interval)
        interval = getattr(self.cfg.sentiment, "refresh_minutes", None) \
            or self.cfg.scheduling.sentiment_refresh_minutes
        return (datetime.now() - self._last_ranking).total_seconds() > interval * 60

    def run_once(self) -> None:
        """Single iteration of the scheduler loop."""
        from trader.sync import full_sync
        from trader.sentiment.factory import refresh_and_store
        from trader.strategy import generate_signals
        from trader.execution import execute_signal
        from trader.universe import get_verified_universe
        from trader.ranking import rank_symbols, select_candidates
        from trader.options_planner import plan_trade

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

        # Ranking + trade planning (runs after sentiment refresh)
        if self._should_rank():
            try:
                universe = get_verified_universe(self.client)
                ranked = rank_symbols(universe)
                candidates = select_candidates(ranked)
                self._last_ranking = datetime.now()
                log.info("Ranking done: %d ranked, %d candidates.", len(ranked), len(candidates))

                # Generate trade plans for each candidate
                dry_run = self.cfg.dry_run
                approve_mode = self._is_approve_mode()

                for candidate in candidates:
                    try:
                        plan = plan_trade(candidate, self.client)
                        if plan is None or plan.status == "skipped":
                            continue

                        if dry_run:
                            log.info(
                                "Dry-run: would trade %s (%s) max_loss=$%.0f",
                                plan.symbol, plan.strategy,
                                _pricing_field(plan, "max_loss_total"),
                            )
                        elif not approve_mode:
                            # Build a SignalIntent from plan and execute
                            self._execute_plan(plan, execute_signal)
                        else:
                            log.info(
                                "Approve mode: %s plan saved (status=%s)",
                                plan.symbol, plan.status,
                            )
                    except Exception as e:
                        log.error("Planning failed for %s: %s", candidate.symbol, e)
            except Exception as e:
                log.error("Ranking/planning failed: %s", e)

        # Evaluate legacy signals
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

    def _is_approve_mode(self) -> bool:
        with get_db() as db:
            from common.models import BotState
            state = db.query(BotState).first()
            return state.approve_mode if state else True

    def _execute_plan(self, plan, execute_signal_fn) -> None:
        """Convert an approved TradePlan into a SignalIntent and execute."""
        from trader.strategy import SignalIntent
        import json
        try:
            pricing = json.loads(plan.pricing_json or "{}")
            rationale = json.loads(plan.rationale_json or "{}")
            intent = SignalIntent(
                symbol=plan.symbol,
                direction="long" if plan.bias == "bullish" else "bearish",
                instrument=plan.strategy,
                score=rationale.get("score_total", 0.0),
                max_risk_usd=pricing.get("max_loss_total", 0.0),
                explanation=f"From trade plan id={plan.id}",
                components=rationale.get("components", {}),
                regime="risk_on" if plan.bias == "bullish" else "risk_off",
            )
            result = execute_signal_fn(intent, self.client)
            if result:
                with get_db() as db:
                    p = db.query(type(plan)).filter(type(plan).id == plan.id).first()
                    if p:
                        p.status = "submitted"
        except Exception as e:
            log.error("Failed to execute plan %s for %s: %s", plan.id, plan.symbol, e)

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
