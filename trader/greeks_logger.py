"""Structured JSON logging for Greeks decisions (post-trade analysis)."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any, Optional

from common.logging import get_logger
from trader.greeks import OptionChainGreeks
from trader.greeks_gate import GateResult
from trader.strike_selector import SpreadSelection, StrikeSelectionCriteria


def _default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    if is_dataclass(o):
        return asdict(o)
    return str(o)


class GreeksLogger:
    """Emits structured JSON log lines tagged with an event type."""

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger or get_logger("greeks")

    def _emit(self, event: str, payload: dict) -> None:
        payload = {"event": event, "ts": datetime.utcnow().isoformat(), **payload}
        try:
            self.logger.info(json.dumps(payload, default=_default))
        except Exception as e:  # pragma: no cover — logging should never kill the flow
            self.logger.error("greeks_logger serialize failed: %s", e)

    # ── chain fetch ───────────────────────────────────────

    def log_chain_fetch(self, chain: OptionChainGreeks) -> None:
        ivs = [s.implied_vol for s in (chain.calls + chain.puts) if s.implied_vol is not None]
        self._emit("chain_fetch", {
            "symbol": chain.symbol,
            "expiration": chain.expiration,
            "underlying_price": chain.underlying_price,
            "num_calls": len(chain.calls),
            "num_puts": len(chain.puts),
            "iv_rank": chain.iv_rank,
            "historical_vol": chain.historical_vol,
            "iv_min": round(min(ivs), 4) if ivs else None,
            "iv_max": round(max(ivs), 4) if ivs else None,
            "iv_avg": round(sum(ivs) / len(ivs), 4) if ivs else None,
        })

    # ── strike selection ──────────────────────────────────

    def log_strike_selection(
        self,
        spread: Optional[SpreadSelection],
        criteria: StrikeSelectionCriteria,
        candidates_evaluated: int,
        reason_if_none: Optional[str] = None,
    ) -> None:
        payload: dict = {
            "candidates_evaluated": candidates_evaluated,
            "iv_environment": criteria.iv_environment,
            "long_delta_target": criteria.long_delta_target,
            "short_delta_target": criteria.short_delta_target,
            "preferred_spread_width": criteria.preferred_spread_width,
        }
        if spread is None:
            payload["selected"] = False
            payload["reason"] = reason_if_none or "no valid strike combination"
        else:
            payload.update({
                "selected": True,
                "direction": spread.direction,
                "strategy_type": spread.strategy_type,
                "long_strike": spread.long_strike,
                "short_strike": spread.short_strike,
                "long_delta": spread.long_delta,
                "short_delta": spread.short_delta,
                "net_delta": spread.net_delta,
                "net_theta": spread.net_theta,
                "net_vega": spread.net_vega,
                "net_gamma": spread.net_gamma,
                "long_iv": spread.long_iv,
                "short_iv": spread.short_iv,
                "estimated_debit": spread.estimated_debit,
                "estimated_credit": spread.estimated_credit,
                "spread_width": spread.spread_width,
                "max_profit": spread.max_profit,
                "max_loss": spread.max_loss,
                "return_on_capital": spread.return_on_capital,
                "buffer_pct": spread.buffer_pct,
                "underlying_price": spread.underlying_price,
                "iv_rank": spread.iv_rank,
            })
        self._emit("strike_selection", payload)

    # ── gate result ───────────────────────────────────────

    def log_gate_result(self, gate_result: GateResult, spread: SpreadSelection) -> None:
        self._emit("gate_result", {
            "approved": gate_result.approved,
            "reason": gate_result.reason,
            "checks_passed": gate_result.checks_passed,
            "checks_failed": gate_result.checks_failed,
            "warnings": gate_result.warnings,
            "greeks_summary": gate_result.greeks_summary,
        })

    # ── entry snapshot ────────────────────────────────────

    def log_greeks_at_entry(self, spread: SpreadSelection, trade_id: str) -> None:
        self._emit("entry_snapshot", {
            "trade_id": trade_id,
            "direction": spread.direction,
            "strategy_type": spread.strategy_type,
            "long_strike": spread.long_strike,
            "short_strike": spread.short_strike,
            "expiration": spread.expiration,
            "long_delta": spread.long_delta,
            "short_delta": spread.short_delta,
            "net_delta": spread.net_delta,
            "net_theta": spread.net_theta,
            "net_vega": spread.net_vega,
            "net_gamma": spread.net_gamma,
            "long_iv": spread.long_iv,
            "short_iv": spread.short_iv,
            "iv_rank": spread.iv_rank,
            "underlying_price": spread.underlying_price,
            "estimated_debit": spread.estimated_debit,
            "estimated_credit": spread.estimated_credit,
            "max_profit": spread.max_profit,
            "max_loss": spread.max_loss,
            "return_on_capital": spread.return_on_capital,
        })
