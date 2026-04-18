"""Configuration loader with Pydantic validation."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

_CFG_PATH_ENV = "MARKET_AI_CONFIG"
_DEFAULT_CFG = "config.yaml"
_EXAMPLE_CFG = "config.example.yaml"


# ── sub-models ──────────────────────────────────────────────

class IbkrConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 1
    account: str = ""


class DbConfig(BaseModel):
    url: Optional[str] = None   # preferred: full SQLAlchemy URL (postgresql+psycopg://...)
    path: str = "app.db"        # SQLite fallback when url is not set


class SchedulingConfig(BaseModel):
    sentiment_refresh_minutes: int = 30
    signal_eval_minutes: int = 15
    rebalance_time_local: str = "09:45"


class UniverseConfig(BaseModel):
    min_mcap: float = 1_000_000_000
    min_dollar_volume: float = 10_000_000
    min_price: float = 5.0
    exclude_leveraged_etfs: bool = True
    max_spread_pct: float = 0.5


class StrategyWeights(BaseModel):
    trend: float = 0.35
    momentum: float = 0.25
    volatility: float = 0.15
    sentiment: float = 0.25


class RegimeConfig(BaseModel):
    vol_threshold: float = 25.0


class StrategyConfig(BaseModel):
    weights: StrategyWeights = StrategyWeights()
    regime: RegimeConfig = RegimeConfig()
    timeframes: List[str] = ["1D", "1H"]
    max_holding_days: int = 20


class OptionsConfig(BaseModel):
    enabled: bool = True
    dte_min: int = 7
    dte_max: int = 21
    min_open_interest: int = 100
    max_option_spread_pct: float = 10.0
    max_spread_width: int = 5


class RiskConfig(BaseModel):
    max_drawdown_pct: float = 50
    max_risk_per_trade_pct: float = 5
    max_positions: int = 5
    require_positive_cash: bool = True


class ExecutionConfig(BaseModel):
    order_type: str = "LIMIT"
    tif: str = "DAY"
    fill_timeout_seconds: int = 120
    requote_attempts: int = 2


class SafetyConfig(BaseModel):
    data_stale_minutes: int = 15
    trade_when_stale: bool = False


class FeaturesConfig(BaseModel):
    approve_mode_default: bool = True


# ── root ────────────────────────────────────────────────────

class AppConfig(BaseModel):
    mode: str = "PAPER"
    ibkr: IbkrConfig = IbkrConfig()
    db: DbConfig = DbConfig()
    scheduling: SchedulingConfig = SchedulingConfig()
    universe: UniverseConfig = UniverseConfig()
    strategy: StrategyConfig = StrategyConfig()
    options: OptionsConfig = OptionsConfig()
    risk: RiskConfig = RiskConfig()
    execution: ExecutionConfig = ExecutionConfig()
    safety: SafetyConfig = SafetyConfig()
    features: FeaturesConfig = FeaturesConfig()

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, v: str) -> str:
        v = v.upper()
        if v not in ("PAPER", "LIVE"):
            raise ValueError("mode must be PAPER or LIVE")
        return v


_cached: Optional[AppConfig] = None


def load_config(path: Optional[str] = None, *, reload: bool = False) -> AppConfig:
    global _cached
    if _cached is not None and not reload:
        return _cached

    if path is None:
        path = os.environ.get(_CFG_PATH_ENV)
    if path is None:
        # try config.yaml, fall back to example
        if Path(_DEFAULT_CFG).exists():
            path = _DEFAULT_CFG
        elif Path(_EXAMPLE_CFG).exists():
            path = _EXAMPLE_CFG
        else:
            _cached = AppConfig()
            return _cached

    with open(path, "r") as f:
        raw = yaml.safe_load(f) or {}

    _cached = AppConfig(**raw)
    return _cached


def get_config() -> AppConfig:
    return load_config()
