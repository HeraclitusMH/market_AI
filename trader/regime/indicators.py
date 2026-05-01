"""Regime pillar indicator computations."""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd

from trader.regime.models import PillarScore


def compute_trend_score(spy_bars: Optional[pd.DataFrame], cfg) -> PillarScore:
    """Compute trend score from SPY daily bars. Returns 0-100 score."""
    components: Dict[str, float] = {}

    if spy_bars is None or len(spy_bars) < cfg.sma_period + 10:
        return PillarScore(
            name="trend", score=50.0, weight=0.0, weighted_contribution=0.0,
            confidence=0.0, data_available=False, reason="Insufficient SPY bars",
        )

    close = float(spy_bars["close"].iloc[-1])
    sma200 = float(spy_bars["close"].rolling(cfg.sma_period).mean().iloc[-1])
    ema20 = float(spy_bars["close"].ewm(span=cfg.ema_fast, adjust=False).mean().iloc[-1])
    ema50 = float(spy_bars["close"].ewm(span=cfg.ema_slow, adjust=False).mean().iloc[-1])

    components.update({"close": close, "sma200": sma200, "ema20": ema20, "ema50": ema50})

    above_sma200 = close > sma200
    ema_bullish = ema20 > ema50

    if above_sma200 and ema_bullish:
        base_score = 100.0
    elif above_sma200 and not ema_bullish:
        base_score = 60.0
    elif not above_sma200 and ema_bullish:
        base_score = 40.0
    else:
        base_score = 0.0
    components["base_score"] = base_score

    distance_pct = (close - sma200) / sma200
    components["distance_from_sma200_pct"] = distance_pct
    chop_penalty = -10.0 if abs(distance_pct) < 0.01 else 0.0
    components["chop_penalty"] = chop_penalty

    sma200_10d_ago = float(spy_bars["close"].rolling(cfg.sma_period).mean().iloc[-10])
    slope_positive = sma200 > sma200_10d_ago
    slope_modifier = 10.0 if slope_positive else -10.0
    components["sma200_slope_positive"] = float(slope_positive)
    components["slope_modifier"] = slope_modifier

    final_score = max(0.0, min(100.0, base_score + chop_penalty + slope_modifier))
    components["final_score"] = final_score

    reason = _trend_reason(above_sma200, ema_bullish, slope_positive)
    return PillarScore(
        name="trend", score=final_score, weight=0.0, weighted_contribution=0.0,
        components=components, confidence=1.0, data_available=True, reason=reason,
    )


def _trend_reason(above_sma200: bool, ema_bullish: bool, slope_positive: bool) -> str:
    if above_sma200 and ema_bullish:
        return "Full uptrend: price above SMA200, EMA20 > EMA50"
    if above_sma200 and not ema_bullish:
        return "Pullback in uptrend: above SMA200 but short-term weakness"
    if not above_sma200 and ema_bullish:
        return "Recovery attempt: below SMA200 but short-term momentum improving"
    return "Confirmed downtrend: below SMA200, EMA20 < EMA50"


def compute_breadth_score(universe_bars: Dict[str, Optional[pd.DataFrame]], cfg) -> PillarScore:
    """% of universe above their own N-day MA. Returns 0-100 score."""
    components: Dict[str, float] = {}

    above_count = 0
    total_evaluated = 0

    for symbol, df in universe_bars.items():
        if df is None or len(df) < cfg.ma_period + 5:
            continue
        close = df["close"].iloc[-1]
        sma = df["close"].rolling(cfg.ma_period).mean().iloc[-1]
        if pd.isna(sma):
            continue
        total_evaluated += 1
        if close > sma:
            above_count += 1

    if total_evaluated == 0:
        return PillarScore(
            name="breadth", score=50.0, weight=0.0, weighted_contribution=0.0,
            confidence=0.0, data_available=False,
            reason="No symbols with sufficient bar history",
        )

    breadth_pct = above_count / total_evaluated
    components.update({
        "above_count": float(above_count),
        "total_evaluated": float(total_evaluated),
        "breadth_pct": breadth_pct,
    })

    confidence = min(1.0, total_evaluated / cfg.min_symbols_required)

    if breadth_pct >= cfg.strong_threshold:
        score = 100.0
    elif breadth_pct >= cfg.moderate_threshold:
        pct = (breadth_pct - cfg.moderate_threshold) / (cfg.strong_threshold - cfg.moderate_threshold)
        score = 60.0 + pct * 39.0
    elif breadth_pct >= cfg.weak_threshold:
        pct = (breadth_pct - cfg.weak_threshold) / (cfg.moderate_threshold - cfg.weak_threshold)
        score = 20.0 + pct * 39.0
    else:
        pct = breadth_pct / cfg.weak_threshold if cfg.weak_threshold > 0 else 0.0
        score = pct * 19.0

    components["final_score"] = score

    return PillarScore(
        name="breadth", score=score, weight=0.0, weighted_contribution=0.0,
        components=components, confidence=confidence, data_available=True,
        reason=f"{breadth_pct*100:.1f}% of {total_evaluated} symbols above {cfg.ma_period}-day MA",
    )


def compute_volatility_score(
    spy_bars: Optional[pd.DataFrame],
    vix_bars: Optional[pd.DataFrame],
    cfg,
) -> PillarScore:
    """Compute vol score from VIX level, term structure, and realized vol. Returns 0-100."""
    components: Dict[str, float] = {}
    confidence = 1.0

    # Sub-indicator 1: VIX Level (0-35 points)
    vix_score = 20.0
    if vix_bars is not None and len(vix_bars) >= 5:
        vix_close = float(vix_bars["close"].iloc[-1])
        components["vix_level"] = vix_close
        if vix_close <= cfg.vix_low:
            vix_score = 35.0
        elif vix_close <= cfg.vix_moderate:
            pct = (cfg.vix_moderate - vix_close) / (cfg.vix_moderate - cfg.vix_low)
            vix_score = 25.0 + pct * 10.0
        elif vix_close <= cfg.vix_elevated:
            pct = (cfg.vix_elevated - vix_close) / (cfg.vix_elevated - cfg.vix_moderate)
            vix_score = 15.0 + pct * 10.0
        elif vix_close <= cfg.vix_high:
            pct = (cfg.vix_high - vix_close) / (cfg.vix_high - cfg.vix_elevated)
            vix_score = 5.0 + pct * 10.0
        else:
            vix_score = 0.0
    else:
        components["vix_level"] = -1.0
        components["vix_note"] = 1.0  # unavailable flag
        confidence *= 0.6
    components["vix_score"] = vix_score

    # Sub-indicator 2: VIX term structure (0-30 points, approx via VIX vs SMA20)
    term_score = 15.0
    if vix_bars is not None and len(vix_bars) >= 25:
        vix_close = float(vix_bars["close"].iloc[-1])
        vix_sma20 = float(vix_bars["close"].rolling(20).mean().iloc[-1])
        if not pd.isna(vix_sma20) and vix_sma20 > 0:
            vix_ratio = vix_close / vix_sma20
            components["vix_vs_sma20_ratio"] = vix_ratio
            if vix_ratio < 0.90:
                term_score = 30.0
            elif vix_ratio < 0.95:
                term_score = 25.0
            elif vix_ratio < 1.05:
                term_score = 15.0
            elif vix_ratio < 1.15:
                term_score = 8.0
            else:
                term_score = 0.0
    components["term_structure_score"] = term_score

    # Sub-indicator 3: Realized vol (0-35 points)
    real_vol_score = 20.0
    if spy_bars is not None and len(spy_bars) >= cfg.realized_vol_period + 5:
        returns = spy_bars["close"].pct_change().dropna()
        realized_vol = float(returns.iloc[-cfg.realized_vol_period:].std() * (252 ** 0.5) * 100)
        components["realized_vol_annualized"] = realized_vol
        if realized_vol <= cfg.realized_vol_low:
            real_vol_score = 35.0
        elif realized_vol <= cfg.realized_vol_moderate:
            pct = (cfg.realized_vol_moderate - realized_vol) / (cfg.realized_vol_moderate - cfg.realized_vol_low)
            real_vol_score = 22.0 + pct * 13.0
        elif realized_vol <= cfg.realized_vol_elevated:
            pct = (cfg.realized_vol_elevated - realized_vol) / (cfg.realized_vol_elevated - cfg.realized_vol_moderate)
            real_vol_score = 10.0 + pct * 12.0
        else:
            real_vol_score = max(0.0, 10.0 - (realized_vol - cfg.realized_vol_elevated) * 0.5)
    components["realized_vol_score"] = real_vol_score

    total_score = vix_score + term_score + real_vol_score
    final_score = max(0.0, min(100.0, total_score))
    components["final_score"] = final_score

    vix_display = components.get("vix_level", -1)
    rv_display = components.get("realized_vol_annualized", -1)
    reason = f"VIX={vix_display:.1f}" if vix_display >= 0 else "VIX=N/A"
    if rv_display >= 0:
        reason += f", RealVol={rv_display:.1f}%"

    return PillarScore(
        name="volatility", score=final_score, weight=0.0, weighted_contribution=0.0,
        components=components, confidence=confidence,
        data_available=(spy_bars is not None and len(spy_bars) >= cfg.realized_vol_period + 5),
        reason=reason,
    )


def compute_credit_stress_score(
    hyg_bars: Optional[pd.DataFrame],
    lqd_bars: Optional[pd.DataFrame],
    cfg,
) -> PillarScore:
    """HYG/LQD ratio vs its SMA. Returns 0-100 (100=no stress, 0=severe stress)."""
    components: Dict[str, float] = {}

    if hyg_bars is None or lqd_bars is None:
        return PillarScore(
            name="credit_stress", score=50.0, weight=0.0, weighted_contribution=0.0,
            confidence=0.0, data_available=False, reason="HYG or LQD data unavailable",
        )

    min_required = cfg.ratio_sma_period + 10
    if len(hyg_bars) < min_required or len(lqd_bars) < min_required:
        return PillarScore(
            name="credit_stress", score=50.0, weight=0.0, weighted_contribution=0.0,
            confidence=0.3, data_available=False,
            reason=f"Insufficient history for {cfg.ratio_sma_period}-day ratio SMA",
        )

    min_len = min(len(hyg_bars), len(lqd_bars))
    hyg_close = hyg_bars["close"].values[-min_len:]
    lqd_close = lqd_bars["close"].values[-min_len:]
    lqd_close = np.where(lqd_close == 0, 1e-6, lqd_close)
    ratio_series = hyg_close / lqd_close

    current_ratio = float(ratio_series[-1])
    ratio_sma = float(np.mean(ratio_series[-cfg.ratio_sma_period:]))
    components.update({"current_ratio": current_ratio, "ratio_sma": ratio_sma})

    ratio_rising = bool(ratio_series[-1] > ratio_series[-6]) if len(ratio_series) >= 6 else True
    components["ratio_rising"] = float(ratio_rising)

    deviation_pct = (current_ratio - ratio_sma) / ratio_sma if ratio_sma != 0 else 0.0
    components["deviation_from_sma_pct"] = deviation_pct

    if current_ratio > ratio_sma and ratio_rising:
        score = 100.0
    elif current_ratio > ratio_sma and not ratio_rising:
        score = 70.0
    elif deviation_pct >= -cfg.mild_deviation_pct:
        score = 40.0
    elif deviation_pct >= -cfg.severe_deviation_pct:
        band = cfg.severe_deviation_pct - cfg.mild_deviation_pct
        pct_in = (deviation_pct + cfg.severe_deviation_pct) / band if band > 0 else 0.0
        score = 10.0 + pct_in * 29.0
    else:
        score = 0.0

    score = max(0.0, min(100.0, score))
    components["final_score"] = score

    return PillarScore(
        name="credit_stress", score=score, weight=0.0, weighted_contribution=0.0,
        components=components, confidence=1.0, data_available=True,
        reason=f"HYG/LQD ratio {deviation_pct*100:+.2f}% vs SMA{cfg.ratio_sma_period}",
    )
