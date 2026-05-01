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
    # 1=Live, 2=Frozen, 3=Delayed, 4=Delayed-Frozen. Paper accounts without
    # market-data subscriptions should use 3 (recommended) or 4.
    market_data_type: int = 3


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


class RegimeStrategyConfig(BaseModel):
    vol_threshold: float = 25.0


class StrategyConfig(BaseModel):
    weights: StrategyWeights = StrategyWeights()
    regime: RegimeStrategyConfig = RegimeStrategyConfig()
    timeframes: List[str] = ["1D", "1H"]
    max_holding_days: int = 20


class OptionsConfig(BaseModel):
    enabled: bool = True
    dte_min: int = 7
    dte_max: int = 21
    min_open_interest: int = 100
    max_option_spread_pct: float = 10.0
    max_spread_width: int = 5
    # Planner DTE settings (canonical location; ranking.dte_* kept for backward compat)
    planner_dte_min: int = 21
    planner_dte_max: int = 45
    planner_dte_target: int = 30
    planner_dte_fallback_min: int = 14


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


# ── Sentiment providers ─────────────────────────────────────

class SentimentRssConfig(BaseModel):
    feeds: List[str] = [
        "https://feeds.reuters.com/reuters/businessNews",
        "https://feeds.reuters.com/reuters/marketsNews",
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "https://www.cnbc.com/id/15839135/device/rss/rss.html",
        "https://www.ft.com/rss/home",
        "https://www.economist.com/rss/the-world-this-week.xml",
        "https://seekingalpha.com/market_currents.xml",
        "http://feeds.marketwatch.com/marketwatch/topstories/",
        "https://www.federalreserve.gov/feeds/press_all.xml",
        "https://finance.yahoo.com/rss/",
    ]
    user_agent: str = "MarketAI/1.0 (+https://local)"
    request_timeout_seconds: int = 20
    max_items_per_run: int = 200


class SentimentClaudeConfig(BaseModel):
    enabled: bool = True
    model: str = "claude-3-5-sonnet-latest"
    api_key_env: str = "ANTHROPIC_API_KEY"
    temperature: float = 0.2
    request_timeout_seconds: int = 45
    max_retries: int = 3
    backoff_base_seconds: float = 2.0
    backoff_max_seconds: float = 30.0

    # Input/output sizing
    max_items_per_run: int = 40
    max_chars_per_item: int = 600
    max_output_tokens: int = 4000

    # Dedup
    dedup_cache_days: int = 14

    # Quality filters
    min_confidence_to_use: float = 0.35

    # Budget hard-cap (sentiment-only)
    monthly_budget_eur: float = 10.0
    eur_usd_rate: float = 1.08
    hard_stop_on_budget: bool = True
    daily_budget_fraction: float = 0.12
    max_tokens_per_item_estimate: int = 300


class RankingConfig(BaseModel):
    # Sentiment sub-weights (market/sector/ticker components)
    w_market: float = 0.20
    w_sector: float = 0.30
    w_ticker: float = 0.50
    # Composite factor weights (sum to 1.0; missing factors redistribute proportionally)
    w_sentiment: float = 0.30
    w_momentum_trend: float = 0.25
    w_risk: float = 0.20
    # Deprecated/ignored: liquidity is an eligibility gate, not a score factor.
    w_liquidity: float = 0.15
    w_fundamentals: float = 0.10
    # Selection thresholds (0..1 scale matching composite score range)
    enter_threshold: float = 0.55      # score >= this → bullish bias
    max_candidates_total: int = 3
    fallback_trade_broad_etf: bool = False
    # Cadence controls
    cooldown_hours: int = 6
    max_trades_per_day: int = 3
    # Liquidity floor (USD avg daily dollar volume)
    min_dollar_volume: float = 20_000_000
    # IBKR contract verification cache TTL
    contract_cache_hours: int = 24
    # Deprecated: planner DTE now lives in cfg.options.planner_dte_*; kept for backward compat
    dte_min: int = 21
    dte_max: int = 45
    dte_target: int = 30
    dte_fallback_min: int = 14


class CompositeScoringConfig(BaseModel):
    config_path: str = "trader/composite_scorer/config/scoring_config.yaml"
    use_cache: bool = False


class OptionsBotConfig(BaseModel):
    enabled: bool = True


class EquityBotConfig(BaseModel):
    enabled: bool = True
    long_only: bool = True                   # v1: no shorting
    long_entry_threshold: float = 0.55       # score >= this → enter long
    exit_threshold: float = 0.45            # score < this → exit
    max_positions: int = 5
    risk_per_trade_pct: float = 1.0          # % of equity to risk per position
    atr_stop_multiplier: float = 2.0         # stop = entry - k * ATR(14)
    atr_period: int = 14
    max_holding_days: int = 20
    min_avg_daily_volume: float = 1_000_000
    max_sector_concentration: float = 0.30  # max 30% portfolio in one sector
    entry_order_type: str = "LIMIT"          # LIMIT or MARKET
    risk_off_mode: str = "cash"              # "cash" | "defensive"
    defensive_sectors: List[str] = Field(
        default_factory=lambda: ["Utilities", "Consumer Staples"]
    )


class BotsConfig(BaseModel):
    options_swing: OptionsBotConfig = OptionsBotConfig()
    equity_swing: EquityBotConfig = EquityBotConfig()


class SecuritiesConfig(BaseModel):
    allowed_exchanges: List[str] = ["NYSE", "NASDAQ", "AMEX"]
    min_price: float = 5.0
    min_avg_dollar_volume_20d: float = 10_000_000
    ibkr_verify_enabled: bool = True
    master_csv_path: str = "data/us_listed_master.csv"
    alias_overrides_path: str = "data/manual_alias_overrides.csv"
    verification_cache_hours: int = 24


class FundamentalMetricBounds(BaseModel):
    worst: float
    best: float


class FundamentalPillarConfig(BaseModel):
    weight: float
    metrics: List[str]


class FundamentalsConfig(BaseModel):
    enabled: bool = True
    ttl_days: int = 7  # DB cache TTL — drives the weekly scheduled refresh
    cache_ttl_hours: float = 24
    refresh_days: int = 7  # scheduler tick: how often to bulk-refresh every symbol
    provider: str = "yfinance"
    request_timeout_seconds: float = 15
    neutral_score: float = 50
    min_coverage: float = 0.2  # fraction of universe that must have data for cross-sectional scoring
    pillar_weights: Dict[str, float] = Field(default_factory=lambda: {
        "valuation": 0.25,
        "profitability": 0.30,
        "growth": 0.25,
        "financial_health": 0.20,
    })
    metric_bounds: Dict[str, FundamentalMetricBounds] = Field(default_factory=lambda: {
        "PEEXCLXOR": FundamentalMetricBounds(worst=40, best=5),
        "PRICE2BK": FundamentalMetricBounds(worst=8, best=0.5),
        "EVCUR2EBITDA": FundamentalMetricBounds(worst=25, best=5),
        "PRICE2SALESTTM": FundamentalMetricBounds(worst=15, best=0.5),
        "TTMROEPCT": FundamentalMetricBounds(worst=-5, best=30),
        "TTMROAPCT": FundamentalMetricBounds(worst=-3, best=15),
        "TTMGROSMGN": FundamentalMetricBounds(worst=10, best=60),
        "TTMNPMGN": FundamentalMetricBounds(worst=-5, best=25),
        "REVCHNGYR": FundamentalMetricBounds(worst=-10, best=30),
        "EPSCHNGYR": FundamentalMetricBounds(worst=-20, best=40),
        "REVTRENDGR": FundamentalMetricBounds(worst=-5, best=25),
        "QCURRATIO": FundamentalMetricBounds(worst=0.5, best=3.0),
        "QQUICKRATI": FundamentalMetricBounds(worst=0.3, best=2.5),
        "QTOTD2EQ": FundamentalMetricBounds(worst=3.0, best=0.0),
    })
    pillars: Dict[str, FundamentalPillarConfig] = Field(default_factory=lambda: {
        "valuation": FundamentalPillarConfig(
            weight=0.25,
            metrics=["PEEXCLXOR", "PRICE2BK", "EVCUR2EBITDA", "PRICE2SALESTTM"],
        ),
        "profitability": FundamentalPillarConfig(
            weight=0.30,
            metrics=["TTMROEPCT", "TTMROAPCT", "TTMGROSMGN", "TTMNPMGN"],
        ),
        "growth": FundamentalPillarConfig(
            weight=0.25,
            metrics=["REVCHNGYR", "EPSCHNGYR", "REVTRENDGR"],
        ),
        "financial_health": FundamentalPillarConfig(
            weight=0.20,
            metrics=["QCURRATIO", "QQUICKRATI", "QTOTD2EQ"],
        ),
    })

    @field_validator("pillars")
    @classmethod
    def _validate_pillar_weights(cls, v: Dict[str, FundamentalPillarConfig]) -> Dict[str, FundamentalPillarConfig]:
        total = sum(pillar.weight for pillar in v.values())
        if abs(total - 1.0) > 0.0001:
            raise ValueError(f"fundamentals.pillars weights must sum to 1.0, got {total:.4f}")
        return v

    @field_validator("pillar_weights")
    @classmethod
    def _validate_legacy_pillar_weights(cls, v: Dict[str, float]) -> Dict[str, float]:
        total = sum(v.values())
        if abs(total - 1.0) > 0.0001:
            raise ValueError(f"fundamentals.pillar_weights must sum to 1.0, got {total:.4f}")
        return v


class EquityExitConfig(BaseModel):
    trailing_stop_enabled: bool = True
    initial_stop_method: str = "atr"
    atr_stop_multiplier: float = 2.0
    trailing_activation_r: float = 1.0
    trailing_method: str = "atr"
    trailing_atr_multiplier: float = 1.5
    trailing_percent: float = 0.05
    stop_never_moves_down: bool = True
    max_holding_days: int = 20
    time_decay_warning_days: int = 15
    profit_target_enabled: bool = True
    profit_target_r: float = 3.0
    partial_profit_enabled: bool = True
    partial_profit_r: float = 2.0
    partial_profit_pct: float = 0.50
    score_exit_enabled: bool = True
    score_exit_threshold: float = 0.40
    score_exit_consecutive_cycles: int = 2
    regime_exit_enabled: bool = True
    regime_exit_action: str = "tighten"   # "tighten" | "close"
    regime_tighten_atr_multiplier: float = 1.0
    sector_exit_on_sentiment_flip: bool = False


class OptionsExitConfig(BaseModel):
    dte_exit_threshold: int = 7
    dte_warning_threshold: int = 14
    profit_target_pct: float = 0.50
    profit_target_aggressive_pct: float = 0.75
    max_loss_exit_pct: float = 0.80
    max_loss_floor_pct: float = 1.00
    time_decay_exit_enabled: bool = True
    theta_bleed_threshold: float = 0.60
    score_exit_enabled: bool = True
    score_exit_threshold: float = 0.40
    score_exit_consecutive_cycles: int = 2
    regime_exit_enabled: bool = True
    regime_exit_action: str = "close"
    delta_drift_exit_enabled: bool = True
    max_delta_drift: float = 0.15
    iv_crush_exit_enabled: bool = True
    iv_crush_threshold: float = 0.30


class ExitConfig(BaseModel):
    enabled: bool = True
    check_frequency: str = "every_cycle"
    equity: EquityExitConfig = EquityExitConfig()
    options: OptionsExitConfig = OptionsExitConfig()


class SentimentConfig(BaseModel):
    provider: str = "rss_lexicon"  # rss_lexicon | claude_llm
    refresh_minutes: int = 60
    rss: SentimentRssConfig = SentimentRssConfig()
    claude: SentimentClaudeConfig = SentimentClaudeConfig()

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("rss_lexicon", "claude_llm"):
            raise ValueError("sentiment.provider must be 'rss_lexicon' or 'claude_llm'")
        return v


# ── Enhanced regime config ───────────────────────────────────

class RegimeTrendConfig(BaseModel):
    sma_period: int = 200
    ema_fast: int = 20
    ema_slow: int = 50


class RegimeBreadthConfig(BaseModel):
    ma_period: int = 50
    strong_threshold: float = 0.70
    moderate_threshold: float = 0.50
    weak_threshold: float = 0.30
    min_symbols_required: int = 20
    use_universe: bool = True


class RegimeVolatilityConfig(BaseModel):
    realized_vol_period: int = 20
    vix_low: float = 15.0
    vix_moderate: float = 20.0
    vix_elevated: float = 25.0
    vix_high: float = 30.0
    realized_vol_low: float = 12.0
    realized_vol_moderate: float = 18.0
    realized_vol_elevated: float = 25.0


class RegimeCreditStressConfig(BaseModel):
    lookback_days: int = 20
    ratio_sma_period: int = 50
    mild_deviation_pct: float = 0.02
    severe_deviation_pct: float = 0.05


class RegimeThresholds(BaseModel):
    risk_on_min: int = 65
    risk_off_max: int = 35


class RegimeHysteresis(BaseModel):
    degrade_confirmations: int = 2
    recover_confirmations: int = 3
    min_hold_cycles: int = 1


class RegimeEffectConfig(BaseModel):
    sizing_factor: float = 1.0
    allows_new_equity_entries: bool = True
    allows_new_options_entries: bool = True
    stop_tightening_factor: float = 1.0
    score_threshold_adjustment: float = 0.0


class RegimeEffectsConfig(BaseModel):
    risk_on: RegimeEffectConfig = RegimeEffectConfig()
    risk_reduced: RegimeEffectConfig = RegimeEffectConfig(
        sizing_factor=0.50,
        allows_new_options_entries=False,
        stop_tightening_factor=0.75,
        score_threshold_adjustment=0.10,
    )
    risk_off: RegimeEffectConfig = RegimeEffectConfig(
        sizing_factor=0.0,
        allows_new_equity_entries=False,
        allows_new_options_entries=False,
        stop_tightening_factor=0.50,
        score_threshold_adjustment=0.20,
    )


class RegimeFallbackConfig(BaseModel):
    on_insufficient_data: str = "risk_reduced"
    on_ibkr_offline: str = "risk_reduced"


class RegimeWeights(BaseModel):
    trend: float = 0.30
    breadth: float = 0.25
    volatility: float = 0.25
    credit_stress: float = 0.20


class RegimeConfig(BaseModel):
    enabled: bool = True
    trend_symbol: str = "SPY"
    volatility_symbol: str = "VIX"
    credit_stress_symbols: Dict[str, str] = Field(
        default_factory=lambda: {"high_yield": "HYG", "investment_grade": "LQD"}
    )
    weights: RegimeWeights = RegimeWeights()
    trend: RegimeTrendConfig = RegimeTrendConfig()
    breadth: RegimeBreadthConfig = RegimeBreadthConfig()
    volatility: RegimeVolatilityConfig = RegimeVolatilityConfig()
    credit_stress: RegimeCreditStressConfig = RegimeCreditStressConfig()
    thresholds: RegimeThresholds = RegimeThresholds()
    hysteresis: RegimeHysteresis = RegimeHysteresis()
    effects: RegimeEffectsConfig = RegimeEffectsConfig()
    fallback: RegimeFallbackConfig = RegimeFallbackConfig()


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
    sentiment: SentimentConfig = SentimentConfig()
    ranking: RankingConfig = RankingConfig()
    scoring: CompositeScoringConfig = CompositeScoringConfig()
    fundamentals: FundamentalsConfig = FundamentalsConfig()
    bots: BotsConfig = BotsConfig()
    securities: SecuritiesConfig = SecuritiesConfig()
    exits: ExitConfig = ExitConfig()
    regime: RegimeConfig = RegimeConfig()
    dry_run: bool = False

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, v: str) -> str:
        v = v.upper()
        if v not in ("PAPER", "LIVE"):
            raise ValueError("mode must be PAPER or LIVE")
        return v


_cached: Optional[AppConfig] = None


def _apply_env_overrides(raw: dict) -> dict:
    """Overlay env vars on top of the parsed YAML.

    Precedence: env vars > config.yaml > config.example.yaml.
    Unknown/unset env vars are ignored.
    """
    raw = dict(raw)  # shallow copy so we don't mutate the caller's dict

    # MODE (PAPER / LIVE)
    env_mode = os.environ.get("MODE")
    if env_mode:
        raw["mode"] = env_mode

    # DB URL
    env_db_url = os.environ.get("DATABASE_URL")
    if env_db_url:
        db_section = dict(raw.get("db") or {})
        db_section["url"] = env_db_url
        raw["db"] = db_section

    # IBKR
    ib_section = dict(raw.get("ibkr") or {})
    if os.environ.get("IB_HOST"):
        ib_section["host"] = os.environ["IB_HOST"]
    if os.environ.get("IB_PORT"):
        ib_section["port"] = int(os.environ["IB_PORT"])
    if os.environ.get("IB_CLIENT_ID"):
        ib_section["client_id"] = int(os.environ["IB_CLIENT_ID"])
    if ib_section:
        raw["ibkr"] = ib_section

    # Features
    env_approve = os.environ.get("APPROVE_MODE_DEFAULT")
    if env_approve is not None:
        feat = dict(raw.get("features") or {})
        feat["approve_mode_default"] = env_approve.strip().lower() in ("1", "true", "yes", "on")
        raw["features"] = feat

    # Sentiment provider override
    env_provider = os.environ.get("SENTIMENT_PROVIDER")
    if env_provider:
        sent_section = dict(raw.get("sentiment") or {})
        sent_section["provider"] = env_provider
        raw["sentiment"] = sent_section

    return raw


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

    if path is not None:
        with open(path, "r") as f:
            raw = yaml.safe_load(f) or {}
    else:
        raw = {}

    raw = _apply_env_overrides(raw)
    _cached = AppConfig(**raw)
    return _cached


def get_config() -> AppConfig:
    return load_config()
