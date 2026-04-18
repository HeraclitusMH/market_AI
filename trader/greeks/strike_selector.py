"""Delta-based strike selection for debit and credit spreads.

Replaces the index-based heuristic in execution.py with live-delta targeting,
IV-adjusted criteria, and real bid/ask midpoint pricing.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import List, Optional

from common.logging import get_logger
from trader.greeks.service import GreeksService, GreeksSnapshot, OptionChainGreeks

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


@dataclass
class StrikeSelectionCriteria:
    """Configuration for delta-based strike selection."""
    short_delta_target: float = field(default_factory=lambda: _env_float("GREEKS_SHORT_DELTA_TARGET", 0.20))
    short_delta_min: float = field(default_factory=lambda: _env_float("GREEKS_SHORT_DELTA_MIN", 0.15))
    short_delta_max: float = field(default_factory=lambda: _env_float("GREEKS_SHORT_DELTA_MAX", 0.30))

    long_delta_target: float = field(default_factory=lambda: _env_float("GREEKS_LONG_DELTA_TARGET", 0.40))
    long_delta_min: float = field(default_factory=lambda: _env_float("GREEKS_LONG_DELTA_MIN", 0.30))
    long_delta_max: float = field(default_factory=lambda: _env_float("GREEKS_LONG_DELTA_MAX", 0.50))

    min_spread_width: float = field(default_factory=lambda: _env_float("GREEKS_MIN_SPREAD_WIDTH", 2.50))
    max_spread_width: float = field(default_factory=lambda: _env_float("GREEKS_MAX_SPREAD_WIDTH", 10.0))
    preferred_spread_width: Optional[float] = field(default_factory=lambda: _env_float("GREEKS_PREFERRED_SPREAD_WIDTH", 5.0))

    min_open_interest: int = field(default_factory=lambda: _env_int("GREEKS_MIN_OPEN_INTEREST", 10))
    min_bid: float = field(default_factory=lambda: _env_float("GREEKS_MIN_BID", 0.05))
    max_bid_ask_spread_pct: float = field(default_factory=lambda: _env_float("GREEKS_MAX_BID_ASK_SPREAD_PCT", 0.30))

    min_roc: float = field(default_factory=lambda: _env_float("GREEKS_MIN_ROC", 0.25))

    iv_environment: str = "unknown"  # set by adjust_delta_for_iv


@dataclass
class SpreadSelection:
    """Result of strike selection — legs + Greeks + pricing + metadata."""
    long_strike: float
    short_strike: float
    expiration: str
    right: str  # 'C' or 'P'

    long_delta: float
    short_delta: float
    long_iv: float
    short_iv: float
    net_delta: float
    net_theta: float
    net_vega: float
    net_gamma: float

    long_mid: float
    short_mid: float
    long_bid: float
    long_ask: float
    short_bid: float
    short_ask: float
    spread_width: float
    estimated_debit: Optional[float] = None
    estimated_credit: Optional[float] = None
    max_profit: float = 0.0
    max_loss: float = 0.0
    return_on_capital: float = 0.0
    breakeven: float = 0.0

    strategy_type: str = "debit"  # "debit" or "credit"
    direction: str = "bull"        # "bull" or "bear"

    iv_rank: Optional[float] = None
    underlying_price: float = 0.0
    buffer_pct: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)


class StrikeSelector:
    """Select optimal strikes using live Greeks data."""

    def __init__(self, greeks_service: GreeksService) -> None:
        self.greeks_service = greeks_service

    # ── IV-adjusted criteria ─────────────────────────────

    def adjust_delta_for_iv(
        self,
        base: StrikeSelectionCriteria,
        iv_rank: Optional[float],
    ) -> StrikeSelectionCriteria:
        """Return a new criteria object with delta targets tuned for IV environment.

        Does not mutate the input. When iv_rank is None, returns base unchanged
        with iv_environment tagged "unknown".
        """
        if iv_rank is None:
            return replace(base, iv_environment="unknown")

        if iv_rank < 20:
            return replace(
                base,
                long_delta_target=0.475,
                long_delta_min=0.42,
                long_delta_max=0.55,
                short_delta_target=0.25,
                short_delta_min=0.20,
                short_delta_max=0.35,
                preferred_spread_width=min(base.preferred_spread_width or 5.0, 2.5),
                iv_environment="low_iv_environment",
            )
        if iv_rank < 40:
            return replace(
                base,
                short_delta_target=0.175,
                short_delta_min=0.15,
                short_delta_max=0.20,
                long_delta_target=0.375,
                long_delta_min=0.35,
                long_delta_max=0.40,
                min_roc=max(base.min_roc, 0.25),
                iv_environment="moderate_iv_environment",
            )
        if iv_rank < 60:
            return replace(
                base,
                short_delta_target=0.135,
                short_delta_min=0.12,
                short_delta_max=0.15,
                long_delta_target=0.325,
                long_delta_min=0.30,
                long_delta_max=0.35,
                min_roc=max(base.min_roc, 0.30),
                iv_environment="elevated_iv_environment",
            )
        return replace(
            base,
            short_delta_target=0.125,
            short_delta_min=0.10,
            short_delta_max=0.15,
            long_delta_target=0.30,
            long_delta_min=0.28,
            long_delta_max=0.35,
            min_roc=max(base.min_roc, 0.30),
            iv_environment="extreme_iv_environment",
        )

    # ── debit spreads ─────────────────────────────────────

    def select_debit_spread_strikes(
        self,
        chain: OptionChainGreeks,
        direction: str,
        criteria: Optional[StrikeSelectionCriteria] = None,
    ) -> Optional[SpreadSelection]:
        """Select strikes for a debit spread (bull call or bear put).

        Bull call: long lower call (near ATM), short higher call (OTM).
        Bear put:  long higher put (near ATM), short lower put (OTM).
        """
        if criteria is None:
            criteria = StrikeSelectionCriteria()

        right = "C" if direction == "bull" else "P"
        legs = self._eligible_legs(chain, right, criteria)
        if len(legs) < 2:
            log.debug("debit[%s/%s]: only %d eligible legs", chain.symbol, direction, len(legs))
            return None

        long_candidate = self._pick_by_delta(
            legs,
            criteria.long_delta_target,
            criteria.long_delta_min,
            criteria.long_delta_max,
        )
        if long_candidate is None:
            log.debug("debit[%s/%s]: no long leg matches delta", chain.symbol, direction)
            return None

        short_candidate = self._pick_debit_short_leg(
            legs, long_candidate, direction, criteria
        )
        if short_candidate is None:
            log.debug("debit[%s/%s]: no matching short leg", chain.symbol, direction)
            return None

        return self._build_selection(
            chain=chain,
            long_leg=long_candidate,
            short_leg=short_candidate,
            direction=direction,
            strategy_type="debit",
            criteria=criteria,
        )

    # ── credit spreads ────────────────────────────────────

    def select_credit_spread_strikes(
        self,
        chain: OptionChainGreeks,
        direction: str,
        criteria: Optional[StrikeSelectionCriteria] = None,
    ) -> Optional[SpreadSelection]:
        """Select strikes for a credit spread.

        Bull put:  short higher put (target short delta), long lower put (further OTM).
        Bear call: short lower call (target short delta), long higher call (further OTM).
        """
        if criteria is None:
            criteria = StrikeSelectionCriteria()

        right = "P" if direction == "bull" else "C"
        legs = self._eligible_legs(chain, right, criteria)
        if len(legs) < 2:
            return None

        short_candidate = self._pick_by_delta(
            legs,
            criteria.short_delta_target,
            criteria.short_delta_min,
            criteria.short_delta_max,
        )
        if short_candidate is None:
            return None

        long_candidate = self._pick_credit_long_leg(
            legs, short_candidate, direction, criteria
        )
        if long_candidate is None:
            return None

        return self._build_selection(
            chain=chain,
            long_leg=long_candidate,
            short_leg=short_candidate,
            direction=direction,
            strategy_type="credit",
            criteria=criteria,
        )

    # ── filtering & picking ───────────────────────────────

    def _eligible_legs(
        self,
        chain: OptionChainGreeks,
        right: str,
        criteria: StrikeSelectionCriteria,
    ) -> List[GreeksSnapshot]:
        out = []
        for leg in chain.leg(right):
            if not leg.is_valid:
                continue
            if leg.bid is None or leg.bid < criteria.min_bid:
                continue
            if leg.mid is None or leg.mid <= 0:
                continue
            if (
                leg.open_interest is not None
                and leg.open_interest < criteria.min_open_interest
            ):
                continue
            spread_pct = leg.bid_ask_spread_pct
            if spread_pct is not None and spread_pct > criteria.max_bid_ask_spread_pct:
                continue
            out.append(leg)
        return out

    @staticmethod
    def _pick_by_delta(
        legs: List[GreeksSnapshot],
        target: float,
        low: float,
        high: float,
    ) -> Optional[GreeksSnapshot]:
        """Pick leg whose abs(delta) is closest to target within [low, high]."""
        in_band = [l for l in legs if l.abs_delta is not None and low <= l.abs_delta <= high]
        if not in_band:
            # widen by 0.05 on each side before giving up
            widened = [
                l for l in legs
                if l.abs_delta is not None
                and (low - 0.05) <= l.abs_delta <= (high + 0.05)
            ]
            if not widened:
                return None
            in_band = widened
        return min(in_band, key=lambda l: abs(l.abs_delta - target))

    def _pick_debit_short_leg(
        self,
        legs: List[GreeksSnapshot],
        long_leg: GreeksSnapshot,
        direction: str,
        criteria: StrikeSelectionCriteria,
    ) -> Optional[GreeksSnapshot]:
        """Short leg must be further OTM than long and within width bounds."""
        if direction == "bull":
            candidates = [l for l in legs if l.strike > long_leg.strike]
        else:
            candidates = [l for l in legs if l.strike < long_leg.strike]

        candidates = self._within_width(candidates, long_leg.strike, criteria)
        if not candidates:
            return None
        return self._pick_by_width_preference(
            candidates, long_leg.strike, criteria.preferred_spread_width
        )

    def _pick_credit_long_leg(
        self,
        legs: List[GreeksSnapshot],
        short_leg: GreeksSnapshot,
        direction: str,
        criteria: StrikeSelectionCriteria,
    ) -> Optional[GreeksSnapshot]:
        """Long leg protects the short — further OTM than short."""
        if direction == "bull":
            # bull put: short higher strike put, long lower strike put
            candidates = [l for l in legs if l.strike < short_leg.strike]
        else:
            # bear call: short lower strike call, long higher strike call
            candidates = [l for l in legs if l.strike > short_leg.strike]

        candidates = self._within_width(candidates, short_leg.strike, criteria)
        if not candidates:
            return None
        return self._pick_by_width_preference(
            candidates, short_leg.strike, criteria.preferred_spread_width
        )

    @staticmethod
    def _within_width(
        candidates: List[GreeksSnapshot],
        anchor_strike: float,
        criteria: StrikeSelectionCriteria,
    ) -> List[GreeksSnapshot]:
        return [
            l for l in candidates
            if criteria.min_spread_width <= abs(l.strike - anchor_strike) <= criteria.max_spread_width
        ]

    @staticmethod
    def _pick_by_width_preference(
        candidates: List[GreeksSnapshot],
        anchor_strike: float,
        preferred: Optional[float],
    ) -> GreeksSnapshot:
        if preferred is None:
            return min(candidates, key=lambda l: abs(l.strike - anchor_strike))
        return min(
            candidates,
            key=lambda l: abs(abs(l.strike - anchor_strike) - preferred),
        )

    # ── build the final selection ─────────────────────────

    def _build_selection(
        self,
        chain: OptionChainGreeks,
        long_leg: GreeksSnapshot,
        short_leg: GreeksSnapshot,
        direction: str,
        strategy_type: str,
        criteria: StrikeSelectionCriteria,
    ) -> Optional[SpreadSelection]:
        spread_width = abs(long_leg.strike - short_leg.strike)
        if spread_width <= 0:
            return None

        # Net greeks: long buys +1, short sells -1
        def _net(a, b):
            if a is None or b is None:
                return 0.0
            return a - b

        net_delta = _net(long_leg.delta, short_leg.delta)
        net_theta = _net(long_leg.theta, short_leg.theta)
        net_vega = _net(long_leg.vega, short_leg.vega)
        net_gamma = _net(long_leg.gamma, short_leg.gamma)

        estimated_debit = None
        estimated_credit = None
        max_profit = 0.0
        max_loss = 0.0
        breakeven = 0.0

        if strategy_type == "debit":
            estimated_debit = round(long_leg.mid - short_leg.mid, 2)
            if estimated_debit <= 0:
                return None
            max_loss = round(estimated_debit, 2)
            max_profit = round(spread_width - estimated_debit, 2)
            if max_profit <= 0:
                return None
            if direction == "bull":
                breakeven = round(long_leg.strike + estimated_debit, 2)
            else:
                breakeven = round(long_leg.strike - estimated_debit, 2)
            roc = max_profit / max_loss if max_loss > 0 else 0.0
        else:  # credit
            estimated_credit = round(short_leg.mid - long_leg.mid, 2)
            if estimated_credit <= 0:
                return None
            max_profit = round(estimated_credit, 2)
            max_loss = round(spread_width - estimated_credit, 2)
            if max_loss <= 0:
                return None
            if direction == "bull":  # bull put
                breakeven = round(short_leg.strike - estimated_credit, 2)
            else:                    # bear call
                breakeven = round(short_leg.strike + estimated_credit, 2)
            roc = max_profit / max_loss if max_loss > 0 else 0.0

        if roc < criteria.min_roc:
            log.debug(
                "%s[%s/%s]: ROC %.3f < min %.3f",
                strategy_type, chain.symbol, direction, roc, criteria.min_roc,
            )
            return None

        buffer_pct = 0.0
        if chain.underlying_price > 0:
            buffer_pct = abs(short_leg.strike - chain.underlying_price) / chain.underlying_price

        return SpreadSelection(
            long_strike=long_leg.strike,
            short_strike=short_leg.strike,
            expiration=long_leg.expiration,
            right=long_leg.right,
            long_delta=long_leg.delta,
            short_delta=short_leg.delta,
            long_iv=long_leg.implied_vol,
            short_iv=short_leg.implied_vol,
            net_delta=net_delta,
            net_theta=net_theta,
            net_vega=net_vega,
            net_gamma=net_gamma,
            long_mid=long_leg.mid,
            short_mid=short_leg.mid,
            long_bid=long_leg.bid or 0.0,
            long_ask=long_leg.ask or 0.0,
            short_bid=short_leg.bid or 0.0,
            short_ask=short_leg.ask or 0.0,
            spread_width=round(spread_width, 2),
            estimated_debit=estimated_debit,
            estimated_credit=estimated_credit,
            max_profit=max_profit,
            max_loss=max_loss,
            return_on_capital=round(roc, 4),
            breakeven=breakeven,
            strategy_type=strategy_type,
            direction=direction,
            iv_rank=chain.iv_rank,
            underlying_price=chain.underlying_price,
            buffer_pct=round(buffer_pct, 4),
        )


def calculate_limit_price(spread: SpreadSelection) -> float:
    """Calculate a limit order price from real mid prices.

    For debit spreads the natural price is the net debit (long_mid - short_mid).
    For credit spreads the natural price is the net credit.
    Returns the absolute limit price (always positive).
    """
    if spread.strategy_type == "debit":
        natural = spread.long_mid - spread.short_mid
    else:
        natural = spread.short_mid - spread.long_mid
    return round(max(natural, 0.01), 2)
