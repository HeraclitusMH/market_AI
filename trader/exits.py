"""Position exit management engine.

Called once per bot cycle BEFORE new entry logic. Evaluates all open
TradeManagement rows against the priority-ordered exit rule stack and
returns ExitEvaluation objects for the bot to act on.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from common.config import ExitConfig
from common.models import TradeManagement
from trader.exit_models import ExitEvaluation, ExitIntent
from trader.exit_rules import (
    check_hard_stop,
    check_max_holding_days,
    check_profit_target_full,
    check_partial_profit,
    check_regime_exit,
    check_score_degradation,
    update_trailing_stop,
    check_options_max_loss,
    check_dte_exit,
    check_options_profit_target,
    check_options_regime_exit,
    check_iv_crush_exit,
    check_delta_drift_exit,
    check_options_score_degradation,
    check_theta_bleed,
)

log = logging.getLogger(__name__)


class ExitManager:
    """
    Evaluates all open positions against the exit rule stack.
    Called once per bot cycle, BEFORE new entry logic.
    """

    def __init__(self, config: ExitConfig, db_session, ibkr_client=None):
        self.cfg = config
        self.session = db_session
        self.client = ibkr_client

    def evaluate_all_positions(self, context) -> List[ExitEvaluation]:
        """
        Main entry point. Loads all TradeManagement rows for the bot's
        portfolio_id (if context has one), evaluates each, persists state
        changes, and returns a list of ExitEvaluation objects.

        context must expose:
          .regime  : str
          .ranked  : list of RankedSymbol (optional; used for current score)
          .now     : datetime (UTC)
        """
        if not self.cfg.enabled:
            return []

        now = getattr(context, "now", datetime.now(timezone.utc))
        portfolio_id = getattr(context, "portfolio_id", None)

        q = self.session.query(TradeManagement)
        if portfolio_id:
            q = q.filter(TradeManagement.portfolio_id == portfolio_id)
        open_positions = q.all()

        evaluations: List[ExitEvaluation] = []
        for tm in open_positions:
            try:
                if tm.instrument_type == "equity":
                    ev = self._evaluate_equity(tm, context, now)
                elif tm.instrument_type == "debit_spread":
                    ev = self._evaluate_options(tm, context, now)
                else:
                    log.warning("Unknown instrument_type=%s for %s", tm.instrument_type, tm.symbol)
                    continue

                evaluations.append(ev)
                self.session.commit()
            except Exception as exc:
                log.error("Exit evaluation failed for %s: %s", tm.symbol, exc)
                self.session.rollback()

        return evaluations

    # ── Equity evaluation ────────────────────────────────────────────────────

    def _evaluate_equity(self, tm: TradeManagement, context, now: datetime) -> ExitEvaluation:
        cfg = self.cfg.equity
        ev = ExitEvaluation(symbol=tm.symbol, portfolio_id=tm.portfolio_id, management_id=tm.id)

        current_price = self._get_current_price(tm.symbol)
        if current_price is None:
            ev.warnings.append("Cannot fetch current price; skipping exit eval")
            days_held = (now.date() - tm.entry_date.date()).days
            if days_held > cfg.max_holding_days + 3:
                log.critical(
                    "[exit] %s: NO PRICE DATA and severely overdue (%dd held). "
                    "Manual intervention required.",
                    tm.symbol, days_held,
                )
            return ev

        current_score = self._get_current_score(tm.symbol, getattr(context, "ranked", []))
        bars_df = self._get_bars(tm.symbol)

        # Update high/low water marks and R-multiple
        if current_price > (tm.highest_price_since_entry or tm.entry_price):
            tm.highest_price_since_entry = current_price
        if current_price < (tm.lowest_price_since_entry or tm.entry_price):
            tm.lowest_price_since_entry = current_price

        from trader.exit_rules import compute_r_multiple
        tm.current_r_multiple = compute_r_multiple(tm, current_price)
        tm.days_held = (now.date() - tm.entry_date.date()).days

        # Priority-ordered rule stack
        rules = [
            ("hard_stop", lambda: check_hard_stop(tm, current_price)),
            ("max_holding_days", lambda: check_max_holding_days(tm, cfg, now)),
            ("profit_target_full", lambda: check_profit_target_full(tm, current_price, cfg)),
            ("partial_profit", lambda: check_partial_profit(tm, current_price, cfg)),
            ("regime_change", lambda: check_regime_exit(tm, context.regime, cfg)),
            ("score_degradation", lambda: check_score_degradation(tm, current_score, cfg)),
        ]

        full_exit_triggered = False
        for rule_name, rule_fn in rules:
            ev.rules_evaluated.append(rule_name)
            intent = rule_fn()
            if intent is not None:
                ev.rules_triggered.append(rule_name)
                ev.exit_intents.append(intent)
                ev.should_exit = True
                if not intent.is_partial:
                    full_exit_triggered = True
                    break

        if full_exit_triggered:
            return ev

        # Trailing stop update (management action, not an exit order)
        if bars_df is not None:
            new_stop = update_trailing_stop(tm, current_price, bars_df, cfg)
            if new_stop is not None:
                log.info(
                    "[exit] %s trailing stop: %.2f -> %.2f",
                    tm.symbol, tm.current_stop, new_stop,
                )
                tm.current_stop = new_stop
                ev.stop_updated = True
                ev.new_stop_price = new_stop

        # Regime tightening (side-effect when action=tighten)
        if (
            cfg.regime_exit_enabled
            and cfg.regime_exit_action == "tighten"
            and context.regime == "risk_off"
            and tm.entry_atr
        ):
            tightened = current_price - (tm.entry_atr * cfg.regime_tighten_atr_multiplier)
            if tightened > tm.current_stop:
                tm.current_stop = tightened
                ev.stop_updated = True
                ev.new_stop_price = tightened
                ev.warnings.append(f"Regime risk_off: stop tightened to {tightened:.2f}")

        return ev

    # ── Options evaluation ───────────────────────────────────────────────────

    def _evaluate_options(self, tm: TradeManagement, context, now: datetime) -> ExitEvaluation:
        cfg = self.cfg.options
        ev = ExitEvaluation(symbol=tm.symbol, portfolio_id=tm.portfolio_id, management_id=tm.id)

        current_spread_value = self._get_current_spread_value(tm)
        current_score = self._get_current_score(tm.symbol, getattr(context, "ranked", []))
        current_iv = self._get_current_iv(tm.symbol)
        current_net_delta = self._get_current_net_delta(tm)

        rules = [
            ("max_loss_stop", lambda: check_options_max_loss(tm, current_spread_value, cfg)),
            ("dte_threshold", lambda: check_dte_exit(tm, cfg, now)),
            ("profit_target", lambda: check_options_profit_target(tm, current_spread_value, cfg, now)),
            ("regime_change", lambda: check_options_regime_exit(tm, context.regime, cfg)),
            ("iv_crush", lambda: check_iv_crush_exit(tm, current_iv, cfg)),
            ("delta_drift", lambda: check_delta_drift_exit(tm, current_net_delta, cfg)),
            ("score_degradation", lambda: check_options_score_degradation(tm, current_score, cfg)),
            ("theta_bleed", lambda: check_theta_bleed(tm, current_spread_value, cfg, now)),
        ]

        for rule_name, rule_fn in rules:
            ev.rules_evaluated.append(rule_name)
            intent = rule_fn()
            if intent is not None:
                ev.rules_triggered.append(rule_name)
                ev.exit_intents.append(intent)
                ev.should_exit = True
                break  # options are always a full exit (no partials for spreads)

        return ev

    # ── Market data helpers ──────────────────────────────────────────────────

    def _get_current_price(self, symbol: str) -> Optional[float]:
        try:
            from trader.market_data import fetch_bars
            df = fetch_bars(symbol, "1D", self.client)
            if df is not None and not df.empty:
                return float(df["close"].iloc[-1])
        except Exception as exc:
            log.warning("Cannot fetch price for %s: %s", symbol, exc)
        return None

    def _get_current_spread_value(self, tm: TradeManagement) -> Optional[float]:
        """Fetch mid-price of the options combo from IBKR; returns None if unavailable."""
        if self.client is None:
            return None
        try:
            from trader.greeks import GreeksService
            svc = GreeksService(self.client)
            right = "C" if tm.direction == "long" else "P"
            expiry_str = tm.expiry_date.strftime("%Y%m%d") if tm.expiry_date else ""
            chain = svc.fetch_chain_greeks(tm.symbol, right, expiry_str)
            if chain is None:
                return None
            long_leg = next((l for l in chain.legs if l.strike == tm.long_strike), None)
            short_leg = next((l for l in chain.legs if l.strike == tm.short_strike), None)
            if long_leg and short_leg:
                long_mid = (long_leg.bid + long_leg.ask) / 2
                short_mid = (short_leg.bid + short_leg.ask) / 2
                return max(0.0, long_mid - short_mid)
        except Exception as exc:
            log.warning("Cannot fetch spread value for %s: %s", tm.symbol, exc)
        return None

    def _get_current_score(self, symbol: str, ranked) -> Optional[float]:
        for rs in (ranked or []):
            if rs.symbol == symbol:
                return rs.score_total
        return None

    def _get_current_iv(self, symbol: str) -> Optional[float]:
        if self.client is None:
            return None
        try:
            from trader.greeks import GreeksService
            svc = GreeksService(self.client)
            snap = svc.get_iv_rank(symbol)
            return snap.iv if snap else None
        except Exception:
            return None

    def _get_current_net_delta(self, tm: TradeManagement) -> Optional[float]:
        if self.client is None or tm.long_strike is None:
            return None
        try:
            from trader.greeks import GreeksService
            svc = GreeksService(self.client)
            right = "C" if tm.direction == "long" else "P"
            expiry_str = tm.expiry_date.strftime("%Y%m%d") if tm.expiry_date else ""
            chain = svc.fetch_chain_greeks(tm.symbol, right, expiry_str)
            if chain is None:
                return None
            long_leg = next((l for l in chain.legs if l.strike == tm.long_strike), None)
            short_leg = next((l for l in chain.legs if l.strike == tm.short_strike), None)
            if long_leg and short_leg:
                return long_leg.delta - abs(short_leg.delta)
        except Exception:
            return None
        return None

    def _get_bars(self, symbol: str):
        try:
            from trader.market_data import fetch_bars
            return fetch_bars(symbol, "1D", self.client)
        except Exception:
            return None
