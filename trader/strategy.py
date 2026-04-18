"""Swing strategy: regime filter + per-symbol scoring."""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

from common.config import get_config
from common.db import get_db
from common.logging import get_logger
from common.models import SignalSnapshot, Position
from common.time import utcnow
from trader.indicators import compute_indicators
from trader.market_data import get_latest_bars
from trader.sentiment.scoring import get_latest_market_score, get_latest_sector_score
from trader.universe import get_active_symbols

log = get_logger(__name__)


@dataclass
class SignalIntent:
    symbol: str
    direction: str            # "long" or "bearish"
    instrument: str           # "debit_spread"
    score: float
    max_risk_usd: float
    explanation: str
    components: Dict[str, float]
    regime: str


def check_regime(client=None) -> str:
    """Check market regime using SPY. Returns 'risk_on' or 'risk_off'."""
    cfg = get_config()
    df = get_latest_bars("SPY", "1D", client)
    if df.empty or len(df) < 50:
        log.warning("Insufficient SPY data for regime check.")
        return "risk_off"

    ind = compute_indicators(df)
    if not ind.get("valid"):
        return "risk_off"

    above_sma200 = ind.get("above_sma200", False)
    trend_up = ind.get("trend_up", False)
    rv = ind.get("realized_vol", 99.0)
    vol_ok = rv < cfg.strategy.regime.vol_threshold

    if above_sma200 and trend_up and vol_ok:
        return "risk_on"
    return "risk_off"


def score_symbol(symbol: str, sector: str, regime: str, client=None) -> Optional[SignalIntent]:
    """Score a single symbol. Returns SignalIntent or None."""
    cfg = get_config()
    weights = cfg.strategy.weights

    df = get_latest_bars(symbol, "1D", client)
    if df.empty or len(df) < 50:
        return None

    ind = compute_indicators(df)
    if not ind.get("valid"):
        return None

    # ── Trend score (0..1) ──
    trend = 0.0
    if ind["trend_up"]:
        trend += 0.5
    if ind.get("above_sma200"):
        trend += 0.5

    # ── Momentum score (0..1) ──
    momentum = 0.0
    rsi_val = ind["rsi14"]
    if 40 < rsi_val < 60:
        momentum += 0.3  # neutral zone
    elif rsi_val < 40:
        momentum += 0.6  # potential oversold bounce
    elif rsi_val > 60:
        momentum += 0.1  # already extended

    if ind["macd_bullish"]:
        momentum += 0.4

    momentum = min(1.0, momentum)

    # ── Volatility penalty (0..1, lower is better for entry) ──
    rv = ind["realized_vol"]
    if rv < 15:
        vol_score = 1.0
    elif rv < 25:
        vol_score = 0.7
    elif rv < 40:
        vol_score = 0.4
    else:
        vol_score = 0.1

    # ── Sentiment (normalise -1..1 to 0..1) ──
    mkt_sent = get_latest_market_score()
    sec_sent = get_latest_sector_score(sector) if sector else 0.0
    sent_raw = (mkt_sent + sec_sent) / 2
    sent_score = (sent_raw + 1) / 2  # map to 0..1

    # ── Weighted total ──
    components = {
        "trend": round(trend, 4),
        "momentum": round(momentum, 4),
        "volatility": round(vol_score, 4),
        "sentiment": round(sent_score, 4),
    }

    total = (
        weights.trend * trend
        + weights.momentum * momentum
        + weights.volatility * vol_score
        + weights.sentiment * sent_score
    )
    total = round(total, 4)

    # ── Direction ──
    if regime == "risk_on" and total > 0.5:
        direction = "long"
    elif regime == "risk_off" and total < 0.35:
        direction = "bearish"
    else:
        # hold / skip
        return None

    # ── Max risk ──
    # We'll compute actual equity-based max risk in risk.py; here use placeholder
    max_risk = 0.0  # filled by risk engine

    explanation_parts = []
    if direction == "long":
        explanation_parts.append(f"Bullish: trend={'up' if trend > 0.5 else 'down'}, RSI={rsi_val:.0f}, MACD={'bull' if ind['macd_bullish'] else 'bear'}")
    else:
        explanation_parts.append(f"Bearish: weak trend, RSI={rsi_val:.0f}")
    explanation_parts.append(f"regime={regime}, vol={rv:.1f}%, sentiment={sent_raw:.2f}")

    return SignalIntent(
        symbol=symbol,
        direction=direction,
        instrument="debit_spread",
        score=total,
        max_risk_usd=max_risk,
        explanation="; ".join(explanation_parts),
        components=components,
        regime=regime,
    )


def generate_signals(client=None) -> List[SignalIntent]:
    """Run full signal generation: regime check + score all active symbols."""
    cfg = get_config()
    regime = check_regime(client)
    log.info("Market regime: %s", regime)

    # Get active universe with sector info
    with get_db() as db:
        from common.models import Universe
        tickers = db.query(Universe).filter(Universe.active == True).all()

    signals = []
    for ticker in tickers:
        if ticker.symbol == "SPY":
            continue  # SPY is benchmark, not traded
        intent = score_symbol(ticker.symbol, ticker.sector, regime, client)
        if intent is not None:
            signals.append(intent)

    # Sort by score descending, cap at max_positions
    signals.sort(key=lambda s: s.score, reverse=True)
    max_new = cfg.risk.max_positions
    signals = signals[:max_new]

    # Persist to DB
    now = utcnow()
    with get_db() as db:
        for s in signals:
            db.add(SignalSnapshot(
                timestamp=now,
                symbol=s.symbol,
                score_total=s.score,
                components_json=json.dumps(s.components),
                regime=s.regime,
                action=s.direction,
                explanation=s.explanation,
            ))

    log.info("Generated %d signals (regime=%s).", len(signals), regime)
    return signals
