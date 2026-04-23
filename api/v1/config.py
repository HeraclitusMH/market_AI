"""GET /api/v1/config — read-only config view."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter
from pydantic import BaseModel

from common.config import get_config

router = APIRouter(tags=["v1"])


class ConfigResponse(BaseModel):
    sections: Dict[str, Dict[str, Any]]


@router.get("/config", response_model=ConfigResponse)
def get_config_view():
    cfg = get_config()
    sections: Dict[str, Dict[str, Any]] = {
        "General": {
            "mode": cfg.mode,
            "dry_run": cfg.dry_run,
        },
        "Scheduling": {
            "sentiment_refresh_minutes": cfg.scheduling.sentiment_refresh_minutes,
            "signal_eval_minutes": cfg.scheduling.signal_eval_minutes,
            "rebalance_time_local": cfg.scheduling.rebalance_time_local,
        },
        "Universe": {
            "min_price": cfg.universe.min_price,
            "min_dollar_volume": cfg.universe.min_dollar_volume,
            "exclude_leveraged_etfs": cfg.universe.exclude_leveraged_etfs,
            "max_spread_pct": cfg.universe.max_spread_pct,
        },
        "Strategy": {
            "max_holding_days": cfg.strategy.max_holding_days,
            "regime_vol_threshold": cfg.strategy.regime.vol_threshold,
            "enter_threshold": cfg.ranking.enter_threshold,
            "max_candidates_total": cfg.ranking.max_candidates_total,
        },
        "Options": {
            "enabled": cfg.options.enabled,
            "planner_dte_min": cfg.options.planner_dte_min,
            "planner_dte_max": cfg.options.planner_dte_max,
            "planner_dte_target": cfg.options.planner_dte_target,
            "min_open_interest": cfg.options.min_open_interest,
        },
        "Risk": {
            "max_drawdown_pct": cfg.risk.max_drawdown_pct,
            "max_risk_per_trade_pct": cfg.risk.max_risk_per_trade_pct,
            "max_positions": cfg.risk.max_positions,
            "require_positive_cash": cfg.risk.require_positive_cash,
        },
        "Execution": {
            "order_type": cfg.execution.order_type,
            "tif": cfg.execution.tif,
            "fill_timeout_seconds": cfg.execution.fill_timeout_seconds,
            "requote_attempts": cfg.execution.requote_attempts,
        },
        "Safety": {
            "data_stale_minutes": cfg.safety.data_stale_minutes,
            "trade_when_stale": cfg.safety.trade_when_stale,
            "approve_mode_default": cfg.features.approve_mode_default,
        },
    }
    return ConfigResponse(sections=sections)
