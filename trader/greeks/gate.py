"""Greeks-based trade gate — approve or reject proposed spreads."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from common.logging import get_logger
from trader.greeks.service import OptionChainGreeks
from trader.greeks.strike_selector import SpreadSelection

log = get_logger(__name__)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _default_config() -> Dict[str, Any]:
    return {
        "min_iv_rank_credit_spreads": _env_float("GREEKS_MIN_IV_RANK_CREDIT", 20.0),
        "max_iv_rank_debit_spreads": _env_float("GREEKS_MAX_IV_RANK_DEBIT", 60.0),

        "max_position_delta": _env_float("GREEKS_MAX_POSITION_DELTA", 0.30),
        "min_short_leg_delta": _env_float("GREEKS_MIN_SHORT_LEG_DELTA", 0.10),
        "max_short_leg_delta": _env_float("GREEKS_MAX_SHORT_LEG_DELTA", 0.35),

        "min_theta_per_day": _env_float("GREEKS_MIN_THETA_PER_DAY", 0.01),
        "min_theta_to_delta_ratio": _env_float("GREEKS_MIN_THETA_DELTA_RATIO", 0.02),

        "max_vega_exposure": _env_float("GREEKS_MAX_VEGA_EXPOSURE", 0.50),

        "max_gamma_near_expiry": _env_float("GREEKS_MAX_GAMMA_NEAR_EXPIRY", 0.10),
        "near_expiry_dte": _env_int("GREEKS_NEAR_EXPIRY_DTE", 7),

        "max_bid_ask_spread_pct": _env_float("GREEKS_MAX_BID_ASK_SPREAD_PCT", 0.30),
        "min_open_interest": _env_int("GREEKS_MIN_OPEN_INTEREST", 10),

        "min_credit_received": _env_float("GREEKS_MIN_CREDIT_RECEIVED", 0.10),
        "min_roc": _env_float("GREEKS_MIN_ROC", 0.25),
        "min_buffer_pct": _env_float("GREEKS_MIN_BUFFER_PCT", 0.03),
    }


@dataclass
class GateResult:
    approved: bool
    reason: str
    checks_passed: List[str] = field(default_factory=list)
    checks_failed: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    greeks_summary: Dict[str, Any] = field(default_factory=dict)


class GreeksGate:
    """Gate trades based on Greeks analysis before execution."""

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = {**_default_config(), **(config or {})}

    def evaluate(
        self,
        spread: SpreadSelection,
        chain: OptionChainGreeks,
        strategy_type: str,
    ) -> GateResult:
        """Run every gate check (no short-circuiting) and report a single verdict."""
        passed: List[str] = []
        failed: List[str] = []
        warnings: List[str] = []

        self._check_iv_rank(spread, strategy_type, passed, failed, warnings)
        self._check_delta_range(spread, passed, failed)
        self._check_theta(spread, strategy_type, passed, failed)
        self._check_theta_delta_ratio(spread, passed, failed)
        self._check_vega(spread, passed, failed)
        self._check_gamma_near_expiry(spread, passed, failed)
        self._check_liquidity(spread, passed, failed)
        self._check_pricing(spread, strategy_type, passed, failed)
        self._check_buffer(spread, passed, failed)
        self._compute_risk_score(spread, warnings)

        approved = len(failed) == 0
        reason = "APPROVED" if approved else "; ".join(failed)
        summary = self._greeks_summary(spread)

        result = GateResult(
            approved=approved,
            reason=reason,
            checks_passed=passed,
            checks_failed=failed,
            warnings=warnings,
            greeks_summary=summary,
        )
        if approved:
            log.info(
                "GreeksGate APPROVED %s %s %s/%s: %d checks, %d warnings",
                spread.direction, strategy_type, spread.long_strike, spread.short_strike,
                len(passed), len(warnings),
            )
        else:
            log.info(
                "GreeksGate REJECTED %s: %s",
                spread.strategy_type, reason,
            )
        return result

    # ── individual checks ─────────────────────────────────

    def _check_iv_rank(
        self,
        spread: SpreadSelection,
        strategy_type: str,
        passed: List[str],
        failed: List[str],
        warnings: List[str],
    ) -> None:
        iv_rank = spread.iv_rank
        if iv_rank is None:
            warnings.append("IV_RANK_CHECK: IV Rank unavailable, treating as moderate")
            return

        if strategy_type == "credit":
            threshold = self.config["min_iv_rank_credit_spreads"]
            if iv_rank < threshold:
                failed.append(f"IV_RANK_CHECK: IV Rank {iv_rank:.1f} < min {threshold} for credit")
            else:
                passed.append("IV_RANK_CHECK")
        else:  # debit
            threshold = self.config["max_iv_rank_debit_spreads"]
            if iv_rank > threshold:
                failed.append(f"IV_RANK_CHECK: IV Rank {iv_rank:.1f} > max {threshold} for debit")
            else:
                passed.append("IV_RANK_CHECK")

    def _check_delta_range(
        self,
        spread: SpreadSelection,
        passed: List[str],
        failed: List[str],
    ) -> None:
        abs_short = abs(spread.short_delta) if spread.short_delta is not None else None
        lo = self.config["min_short_leg_delta"]
        hi = self.config["max_short_leg_delta"]
        if abs_short is None:
            failed.append("DELTA_RANGE_CHECK: short leg delta missing")
            return
        if not (lo <= abs_short <= hi):
            failed.append(
                f"DELTA_RANGE_CHECK: short leg delta {abs_short:.3f} outside [{lo}, {hi}]"
            )
            return
        passed.append("DELTA_RANGE_CHECK")

        abs_net = abs(spread.net_delta) if spread.net_delta is not None else 0.0
        max_net = self.config["max_position_delta"]
        if abs_net > max_net:
            failed.append(
                f"DELTA_RANGE_CHECK: position delta {abs_net:.3f} > max {max_net}"
            )

    def _check_theta(
        self,
        spread: SpreadSelection,
        strategy_type: str,
        passed: List[str],
        failed: List[str],
    ) -> None:
        theta = spread.net_theta
        if theta is None:
            failed.append("THETA_CHECK: net theta missing")
            return
        min_theta = self.config["min_theta_per_day"]
        if abs(theta) < min_theta:
            failed.append(
                f"THETA_CHECK: |net theta| {abs(theta):.4f} < min {min_theta}"
            )
            return
        if strategy_type == "credit" and theta < 0:
            failed.append(
                f"THETA_CHECK: credit spread requires positive theta, got {theta:.4f}"
            )
            return
        passed.append("THETA_CHECK")

    def _check_theta_delta_ratio(
        self,
        spread: SpreadSelection,
        passed: List[str],
        failed: List[str],
    ) -> None:
        if spread.net_theta is None or spread.net_delta is None or spread.net_delta == 0:
            passed.append("THETA_DELTA_RATIO_CHECK (skipped: zero delta)")
            return
        ratio = abs(spread.net_theta / spread.net_delta)
        minimum = self.config["min_theta_to_delta_ratio"]
        if ratio < minimum:
            failed.append(
                f"THETA_DELTA_RATIO_CHECK: ratio {ratio:.4f} < min {minimum}"
            )
            return
        passed.append("THETA_DELTA_RATIO_CHECK")

    def _check_vega(
        self,
        spread: SpreadSelection,
        passed: List[str],
        failed: List[str],
    ) -> None:
        if spread.net_vega is None:
            passed.append("VEGA_CHECK (skipped: missing)")
            return
        abs_vega = abs(spread.net_vega)
        max_vega = self.config["max_vega_exposure"]
        if abs_vega > max_vega:
            failed.append(f"VEGA_CHECK: |net vega| {abs_vega:.3f} > max {max_vega}")
            return
        passed.append("VEGA_CHECK")

    def _check_gamma_near_expiry(
        self,
        spread: SpreadSelection,
        passed: List[str],
        failed: List[str],
    ) -> None:
        dte = self._dte(spread.expiration)
        threshold_dte = self.config["near_expiry_dte"]
        if dte is None or dte >= threshold_dte:
            passed.append("GAMMA_NEAR_EXPIRY_CHECK (not near expiry)")
            return
        if spread.net_gamma is None:
            passed.append("GAMMA_NEAR_EXPIRY_CHECK (gamma missing)")
            return
        abs_gamma = abs(spread.net_gamma)
        max_gamma = self.config["max_gamma_near_expiry"]
        if abs_gamma > max_gamma:
            failed.append(
                f"GAMMA_NEAR_EXPIRY_CHECK: |gamma| {abs_gamma:.4f} too high with {dte} DTE"
            )
            return
        passed.append("GAMMA_NEAR_EXPIRY_CHECK")

    def _check_liquidity(
        self,
        spread: SpreadSelection,
        passed: List[str],
        failed: List[str],
    ) -> None:
        max_pct = self.config["max_bid_ask_spread_pct"]
        long_pct = _bid_ask_pct(spread.long_bid, spread.long_ask)
        short_pct = _bid_ask_pct(spread.short_bid, spread.short_ask)
        if long_pct is not None and long_pct > max_pct:
            failed.append(f"LIQUIDITY_CHECK: long leg bid-ask {long_pct:.2%} > {max_pct:.2%}")
            return
        if short_pct is not None and short_pct > max_pct:
            failed.append(f"LIQUIDITY_CHECK: short leg bid-ask {short_pct:.2%} > {max_pct:.2%}")
            return
        passed.append("LIQUIDITY_CHECK")

    def _check_pricing(
        self,
        spread: SpreadSelection,
        strategy_type: str,
        passed: List[str],
        failed: List[str],
    ) -> None:
        min_roc = self.config["min_roc"]
        if spread.return_on_capital < min_roc:
            failed.append(
                f"PRICING_CHECK: ROC {spread.return_on_capital:.3f} < min {min_roc}"
            )
            return
        if strategy_type == "credit":
            min_credit = self.config["min_credit_received"]
            if (spread.estimated_credit or 0.0) < min_credit:
                failed.append(
                    f"PRICING_CHECK: credit {spread.estimated_credit} < min {min_credit}"
                )
                return
        passed.append("PRICING_CHECK")

    def _check_buffer(
        self,
        spread: SpreadSelection,
        passed: List[str],
        failed: List[str],
    ) -> None:
        min_buf = self.config["min_buffer_pct"]
        if spread.buffer_pct < min_buf:
            failed.append(
                f"BUFFER_CHECK: buffer {spread.buffer_pct:.3f} < min {min_buf}"
            )
            return
        passed.append("BUFFER_CHECK")

    def _compute_risk_score(
        self,
        spread: SpreadSelection,
        warnings: List[str],
    ) -> None:
        """Composite 0-100 risk score; warns above 70 but never blocks."""
        score = 0.0
        abs_short = abs(spread.short_delta or 0.0)
        # delta proximity to 0.35 limit
        score += min(40.0, (abs_short / 0.35) * 40.0)
        # IV extremes
        if spread.iv_rank is not None:
            if spread.iv_rank > 80:
                score += 20
            elif spread.iv_rank > 60:
                score += 10
            elif spread.iv_rank < 10:
                score += 15
        # DTE
        dte = self._dte(spread.expiration)
        if dte is not None:
            if dte < 7:
                score += 20
            elif dte < 14:
                score += 10
        # gamma
        if spread.net_gamma is not None:
            score += min(20.0, abs(spread.net_gamma) / 0.10 * 20.0)
        if score > 70:
            warnings.append(f"RISK_SCORE high: {score:.0f}/100")

    # ── helpers ───────────────────────────────────────────

    @staticmethod
    def _dte(expiration: str) -> Optional[int]:
        try:
            dt = datetime.strptime(expiration, "%Y%m%d").date()
        except (TypeError, ValueError):
            return None
        return (dt - datetime.utcnow().date()).days

    @staticmethod
    def _greeks_summary(spread: SpreadSelection) -> Dict[str, Any]:
        return {
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
            "buffer_pct": spread.buffer_pct,
        }


def _bid_ask_pct(bid: float, ask: float) -> Optional[float]:
    if not bid or not ask:
        return None
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    return (ask - bid) / mid
