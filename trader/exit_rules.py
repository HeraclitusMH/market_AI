"""Individual exit rule implementations for equity and options positions."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from trader.exit_models import ExitIntent


# ── Shared helpers ───────────────────────────────────────────────────────────


def compute_r_multiple(tm, current_price: float) -> float:
    """How many R units the position has gained (positive) or lost (negative)."""
    if not tm.risk_per_share or tm.risk_per_share == 0:
        return 0.0
    if tm.direction == "long":
        return (current_price - tm.entry_price) / tm.risk_per_share
    return (tm.entry_price - current_price) / tm.risk_per_share


def compute_current_atr(bars_df: pd.DataFrame, period: int = 14) -> Optional[float]:
    """ATR using the existing indicators.atr function."""
    if bars_df is None or len(bars_df) < period + 1:
        return None
    try:
        from trader.indicators import atr as _atr
        series = _atr(bars_df, period)
        val = series.iloc[-1]
        return float(val) if pd.notna(val) else None
    except Exception:
        return None


# ── EQUITY exit rules ────────────────────────────────────────────────────────


def check_hard_stop(tm, current_price: float) -> Optional[ExitIntent]:
    """Priority 0: price has breached the current stop."""
    triggered = (
        (tm.direction == "long" and current_price <= tm.current_stop)
        or (tm.direction == "short" and current_price >= tm.current_stop)
    )
    if not triggered:
        return None
    return ExitIntent(
        symbol=tm.symbol,
        portfolio_id=tm.portfolio_id,
        instrument_type="equity",
        direction=tm.direction,
        quantity=tm.current_quantity,
        exit_rule="hard_stop",
        exit_reason=f"Price {current_price:.2f} breached stop {tm.current_stop:.2f}",
        urgency="immediate",
        management_id=tm.id,
        priority=0,
    )


def check_max_holding_days(tm, cfg, now: datetime) -> Optional[ExitIntent]:
    """Priority 1: position held too long."""
    days_held = (now.date() - tm.entry_date.date()).days
    if days_held < cfg.max_holding_days:
        return None
    return ExitIntent(
        symbol=tm.symbol,
        portfolio_id=tm.portfolio_id,
        instrument_type="equity",
        direction=tm.direction,
        quantity=tm.current_quantity,
        exit_rule="max_holding_days",
        exit_reason=f"Position held {days_held}d (max={cfg.max_holding_days}d)",
        urgency="normal",
        management_id=tm.id,
        priority=1,
        metadata={"days_held": days_held},
    )


def check_profit_target_full(tm, current_price: float, cfg) -> Optional[ExitIntent]:
    """Priority 2: full profit target in R-multiples."""
    if not cfg.profit_target_enabled:
        return None
    r = compute_r_multiple(tm, current_price)
    if r < cfg.profit_target_r:
        return None
    return ExitIntent(
        symbol=tm.symbol,
        portfolio_id=tm.portfolio_id,
        instrument_type="equity",
        direction=tm.direction,
        quantity=tm.current_quantity,
        exit_rule="profit_target_full",
        exit_reason=f"R-multiple {r:.2f} >= target {cfg.profit_target_r}",
        urgency="normal",
        limit_price=current_price,
        management_id=tm.id,
        priority=2,
        metadata={"r_multiple": r},
    )


def check_partial_profit(tm, current_price: float, cfg) -> Optional[ExitIntent]:
    """Priority 3: partial profit target — fires once per position."""
    if not cfg.partial_profit_enabled or tm.partial_profit_taken:
        return None
    r = compute_r_multiple(tm, current_price)
    if r < cfg.partial_profit_r:
        return None
    close_qty = max(1, int(tm.current_quantity * cfg.partial_profit_pct))
    return ExitIntent(
        symbol=tm.symbol,
        portfolio_id=tm.portfolio_id,
        instrument_type="equity",
        direction=tm.direction,
        quantity=close_qty,
        is_partial=True,
        exit_rule="partial_profit",
        exit_reason=(
            f"Partial take-profit at {r:.2f}R — closing {close_qty}/{tm.current_quantity} shares"
        ),
        urgency="normal",
        limit_price=current_price,
        management_id=tm.id,
        priority=3,
        metadata={"r_multiple": r, "close_qty": close_qty, "move_stop_to_breakeven": True},
    )


def check_regime_exit(tm, current_regime: str, cfg) -> Optional[ExitIntent]:
    """Priority 4: regime flipped to risk_off — close or signal tighten."""
    if not cfg.regime_exit_enabled or current_regime != "risk_off":
        return None
    if tm.entry_regime == "risk_off":
        # Entered during risk_off (defensive position); don't exit on regime alone
        return None
    if cfg.regime_exit_action != "close":
        # "tighten" is handled as a side-effect in ExitManager, not an ExitIntent
        return None
    return ExitIntent(
        symbol=tm.symbol,
        portfolio_id=tm.portfolio_id,
        instrument_type="equity",
        direction=tm.direction,
        quantity=tm.current_quantity,
        exit_rule="regime_change",
        exit_reason="Regime changed to risk_off; action=close",
        urgency="normal",
        management_id=tm.id,
        priority=4,
    )


def check_score_degradation(tm, current_score: Optional[float], cfg) -> Optional[ExitIntent]:
    """Priority 5: composite score persistently below threshold."""
    if not cfg.score_exit_enabled:
        return None
    if current_score is None:
        return None
    if current_score < cfg.score_exit_threshold:
        tm.consecutive_below_threshold = (tm.consecutive_below_threshold or 0) + 1
    else:
        tm.consecutive_below_threshold = 0
    if tm.consecutive_below_threshold < cfg.score_exit_consecutive_cycles:
        return None
    return ExitIntent(
        symbol=tm.symbol,
        portfolio_id=tm.portfolio_id,
        instrument_type="equity",
        direction=tm.direction,
        quantity=tm.current_quantity,
        exit_rule="score_degradation",
        exit_reason=(
            f"Score {current_score:.3f} < {cfg.score_exit_threshold} "
            f"for {tm.consecutive_below_threshold} cycles"
        ),
        urgency="end_of_day",
        management_id=tm.id,
        priority=5,
        metadata={
            "current_score": current_score,
            "consecutive_below": tm.consecutive_below_threshold,
        },
    )


def update_trailing_stop(tm, current_price: float, bars_df, cfg) -> Optional[float]:
    """
    Ratchet the trailing stop upward. Returns the new stop price, or None if
    no update is warranted. Does NOT write to DB — caller is responsible.
    """
    if not cfg.trailing_stop_enabled:
        return None

    r = compute_r_multiple(tm, current_price)

    # Update high-water mark (in-memory only; DB update happens in caller)
    if current_price > (tm.highest_price_since_entry or tm.entry_price):
        tm.highest_price_since_entry = current_price

    if r < cfg.trailing_activation_r:
        tm.trailing_activated = False
        return None

    tm.trailing_activated = True
    highest = tm.highest_price_since_entry or current_price

    if cfg.trailing_method == "atr":
        current_atr = compute_current_atr(bars_df, period=14)
        if current_atr is None:
            return None
        candidate = highest - (current_atr * cfg.trailing_atr_multiplier)
    elif cfg.trailing_method == "percent":
        candidate = highest * (1.0 - cfg.trailing_percent)
    elif cfg.trailing_method == "highest_close":
        candidate = highest - (tm.risk_per_share or 0)
    else:
        return None

    # Only ratchet up
    if cfg.stop_never_moves_down and candidate <= tm.current_stop:
        return None
    # Never push stop above current price (would cause immediate spurious exit)
    if candidate >= current_price:
        return None

    return candidate


# ── OPTIONS exit rules ───────────────────────────────────────────────────────


def check_options_max_loss(tm, current_spread_value: Optional[float], cfg) -> Optional[ExitIntent]:
    """Priority 0: debit spread has lost too large a fraction of the debit paid."""
    if current_spread_value is None or current_spread_value <= 0 or tm.entry_price <= 0:
        return None
    loss_pct = (tm.entry_price - current_spread_value) / tm.entry_price
    if loss_pct < cfg.max_loss_exit_pct:
        return None
    return ExitIntent(
        symbol=tm.symbol,
        portfolio_id=tm.portfolio_id,
        instrument_type="debit_spread",
        direction=tm.direction,
        quantity=tm.current_quantity,
        exit_rule="max_loss_stop",
        exit_reason=(
            f"Spread lost {loss_pct*100:.1f}% of debit "
            f"(threshold={cfg.max_loss_exit_pct*100:.0f}%)"
        ),
        urgency="immediate",
        limit_price=current_spread_value * 0.95,
        management_id=tm.id,
        priority=0,
        metadata={"loss_pct": loss_pct, "current_value": current_spread_value},
    )


def check_dte_exit(tm, cfg, now: datetime) -> Optional[ExitIntent]:
    """Priority 1: DTE too low — gamma risk."""
    if tm.expiry_date is None:
        return None
    dte = (tm.expiry_date.date() - now.date()).days
    if dte > cfg.dte_exit_threshold:
        return None
    return ExitIntent(
        symbol=tm.symbol,
        portfolio_id=tm.portfolio_id,
        instrument_type="debit_spread",
        direction=tm.direction,
        quantity=tm.current_quantity,
        exit_rule="dte_threshold",
        exit_reason=f"DTE={dte} <= threshold={cfg.dte_exit_threshold}; gamma risk",
        urgency="immediate",
        management_id=tm.id,
        priority=1,
        metadata={"dte": dte},
    )


def check_options_profit_target(
    tm,
    current_spread_value: Optional[float],
    cfg,
    now: datetime,
) -> Optional[ExitIntent]:
    """Priority 2: captured sufficient fraction of max profit."""
    if current_spread_value is None or tm.max_profit is None or tm.max_profit <= 0:
        return None
    profit = current_spread_value - tm.entry_price
    if profit <= 0:
        return None
    profit_pct = profit / tm.max_profit
    dte = (tm.expiry_date.date() - now.date()).days if tm.expiry_date else 999
    threshold = (
        cfg.profit_target_aggressive_pct
        if dte < cfg.dte_warning_threshold
        else cfg.profit_target_pct
    )
    if profit_pct < threshold:
        return None
    return ExitIntent(
        symbol=tm.symbol,
        portfolio_id=tm.portfolio_id,
        instrument_type="debit_spread",
        direction=tm.direction,
        quantity=tm.current_quantity,
        exit_rule="profit_target",
        exit_reason=(
            f"Captured {profit_pct*100:.1f}% of max profit "
            f"(threshold={threshold*100:.0f}%, DTE={dte})"
        ),
        urgency="normal",
        limit_price=current_spread_value,
        management_id=tm.id,
        priority=2,
        metadata={"profit_captured_pct": profit_pct, "dte": dte},
    )


def check_options_regime_exit(tm, current_regime: str, cfg) -> Optional[ExitIntent]:
    """Priority 3: regime turned risk_off — options are more sensitive; close."""
    if not cfg.regime_exit_enabled or current_regime != "risk_off":
        return None
    if cfg.regime_exit_action != "close":
        return None
    return ExitIntent(
        symbol=tm.symbol,
        portfolio_id=tm.portfolio_id,
        instrument_type="debit_spread",
        direction=tm.direction,
        quantity=tm.current_quantity,
        exit_rule="regime_change",
        exit_reason="Regime risk_off; options spread closed",
        urgency="normal",
        management_id=tm.id,
        priority=3,
    )


def check_iv_crush_exit(tm, current_iv: Optional[float], cfg) -> Optional[ExitIntent]:
    """Priority 4: IV has collapsed from entry — debit spread edge eroded."""
    if not cfg.iv_crush_exit_enabled:
        return None
    if tm.entry_iv is None or current_iv is None or tm.entry_iv <= 0:
        return None
    iv_drop = (tm.entry_iv - current_iv) / tm.entry_iv
    if iv_drop < cfg.iv_crush_threshold:
        return None
    return ExitIntent(
        symbol=tm.symbol,
        portfolio_id=tm.portfolio_id,
        instrument_type="debit_spread",
        direction=tm.direction,
        quantity=tm.current_quantity,
        exit_rule="iv_crush",
        exit_reason=(
            f"IV dropped {iv_drop*100:.1f}% from entry "
            f"({tm.entry_iv:.2f} -> {current_iv:.2f})"
        ),
        urgency="normal",
        management_id=tm.id,
        priority=4,
        metadata={"entry_iv": tm.entry_iv, "current_iv": current_iv, "iv_drop_pct": iv_drop},
    )


def check_delta_drift_exit(tm, current_net_delta: Optional[float], cfg) -> Optional[ExitIntent]:
    """Priority 5: net delta has drifted too far from entry — thesis broken."""
    if not cfg.delta_drift_exit_enabled:
        return None
    if tm.entry_net_delta is None or current_net_delta is None:
        return None
    drift = abs(current_net_delta - tm.entry_net_delta)
    if drift < cfg.max_delta_drift:
        return None
    return ExitIntent(
        symbol=tm.symbol,
        portfolio_id=tm.portfolio_id,
        instrument_type="debit_spread",
        direction=tm.direction,
        quantity=tm.current_quantity,
        exit_rule="delta_drift",
        exit_reason=(
            f"Net delta drifted {drift:.3f} from entry "
            f"(entry={tm.entry_net_delta:.3f}, current={current_net_delta:.3f})"
        ),
        urgency="normal",
        management_id=tm.id,
        priority=5,
        metadata={
            "entry_delta": tm.entry_net_delta,
            "current_delta": current_net_delta,
            "drift": drift,
        },
    )


def check_options_score_degradation(
    tm,
    current_score: Optional[float],
    cfg,
) -> Optional[ExitIntent]:
    """Priority 6: composite score degraded for multiple consecutive cycles."""
    if not cfg.score_exit_enabled:
        return None
    if current_score is None:
        return None
    if current_score < cfg.score_exit_threshold:
        tm.consecutive_below_threshold = (tm.consecutive_below_threshold or 0) + 1
    else:
        tm.consecutive_below_threshold = 0
    if tm.consecutive_below_threshold < cfg.score_exit_consecutive_cycles:
        return None
    return ExitIntent(
        symbol=tm.symbol,
        portfolio_id=tm.portfolio_id,
        instrument_type="debit_spread",
        direction=tm.direction,
        quantity=tm.current_quantity,
        exit_rule="score_degradation",
        exit_reason=(
            f"Score {current_score:.3f} < {cfg.score_exit_threshold} "
            f"for {tm.consecutive_below_threshold} cycles"
        ),
        urgency="end_of_day",
        management_id=tm.id,
        priority=6,
        metadata={"current_score": current_score},
    )


def check_theta_bleed(
    tm,
    current_spread_value: Optional[float],
    cfg,
    now: datetime,
) -> Optional[ExitIntent]:
    """Priority 7: unrealized loss as fraction of remaining theta is too high."""
    if not cfg.time_decay_exit_enabled:
        return None
    if current_spread_value is None or tm.max_loss is None or tm.max_loss <= 0:
        return None
    if tm.expiry_date is None:
        return None

    dte = (tm.expiry_date.date() - now.date()).days
    if dte <= 0:
        return None

    # Fraction of debit already lost relative to max possible loss
    unrealized_loss = max(0.0, tm.entry_price - current_spread_value)
    theta_remaining_proxy = tm.max_loss * (dte / max(1, 30))  # rough: DTE/30 of max loss
    if theta_remaining_proxy <= 0:
        return None

    bleed_ratio = unrealized_loss / theta_remaining_proxy
    if bleed_ratio < cfg.theta_bleed_threshold:
        return None

    return ExitIntent(
        symbol=tm.symbol,
        portfolio_id=tm.portfolio_id,
        instrument_type="debit_spread",
        direction=tm.direction,
        quantity=tm.current_quantity,
        exit_rule="theta_bleed",
        exit_reason=(
            f"Theta bleed ratio {bleed_ratio:.2f} >= {cfg.theta_bleed_threshold} "
            f"(DTE={dte})"
        ),
        urgency="end_of_day",
        management_id=tm.id,
        priority=7,
        metadata={"bleed_ratio": bleed_ratio, "dte": dte},
    )
