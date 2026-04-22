"""EquitySwingBot — stock share swing trading strategy plugin."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from common.config import get_config
from common.db import get_db
from common.logging import get_logger
from common.models import EquitySnapshot, Order
from bots.base_bot import (
    BaseBot, BotContext, BotRunResult, Candidate, ScoreBreakdown, TradeIntent,
)
from trader.strategy import score_symbol
from trader.market_data import get_latest_bars
from trader.indicators import atr as compute_atr

log = get_logger(__name__)

# Sectors that may be traded defensively in risk-off mode
_DEFENSIVE_SECTORS = frozenset(["Utilities", "Consumer Staples"])


class EquitySwingBot(BaseBot):
    """Buys/sells stock shares using ATR-based position sizing.

    v1: long-only. Shorting requires explicit config opt-in (long_only=False).
    """

    bot_id = "equity_swing"
    instrument_type = "equity"

    def build_candidates(self, context: BotContext) -> List[Candidate]:
        cfg = get_config()
        equity_cfg = cfg.bots.equity_swing

        # risk-off + cash mode: no new positions
        if context.regime == "risk_off" and equity_cfg.risk_off_mode == "cash":
            log.info("[equity_swing] Risk-off + cash mode — no new candidates.")
            return []

        candidates = []
        for item in context.universe:
            if item.symbol == "SPY":
                continue
            if not item.verified:
                continue
            # Restrict to stocks (plus ETFs if configured)
            if item.type not in ("STK", "ETF"):
                continue
            if item.type == "ETF" and not equity_cfg.long_only:
                # In long-only mode ETFs are fine; in short mode, we skip complex ETFs
                pass

            # Defensive-only filter in risk-off mode
            if context.regime == "risk_off" and equity_cfg.risk_off_mode == "defensive":
                defensive = set(equity_cfg.defensive_sectors)
                if item.sector not in defensive:
                    continue

            candidates.append(Candidate(
                symbol=item.symbol,
                sector=item.sector,
                source=item.sources[0] if item.sources else "core",
                verified=item.verified,
            ))
        return candidates

    def score_candidate(
        self,
        candidate: Candidate,
        context: BotContext,
    ) -> Optional[ScoreBreakdown]:
        cfg = get_config()
        equity_cfg = cfg.bots.equity_swing

        intent = score_symbol(
            candidate.symbol, candidate.sector, context.regime, context.client
        )

        # Fetch bars to compute ATR and last price for sizing
        df = get_latest_bars(candidate.symbol, "1D", context.client)
        atr_val: Optional[float] = None
        last_price: Optional[float] = None

        if not df.empty and len(df) >= equity_cfg.atr_period + 1:
            atr_series = compute_atr(df, equity_cfg.atr_period)
            atr_val = float(atr_series.iloc[-1])
            last_price = float(df["close"].iloc[-1])

        if intent is None:
            # Even if score_symbol skips, keep a zero-score breakdown so we
            # have ATR/price for any holding exit checks later; direction=None
            # means no new entry.
            return None

        return ScoreBreakdown(
            trend=intent.components.get("trend", 0.0),
            momentum=intent.components.get("momentum", 0.0),
            volatility=intent.components.get("volatility", 0.0),
            sentiment=intent.components.get("sentiment", 0.0),
            final_score=intent.score,
            direction=intent.direction,
            explanations=[intent.explanation],
            components=intent.components,
            atr14=atr_val,
            last_price=last_price,
        )

    def select_trades(
        self,
        ranked: List[Tuple[Candidate, ScoreBreakdown]],
        context: BotContext,
    ) -> List[TradeIntent]:
        cfg = get_config()
        equity_cfg = cfg.bots.equity_swing

        if not cfg.bots.equity_swing.enabled:
            log.info("[equity_swing] Bot is disabled — no trades selected.")
            return []

        nav = _get_nav()
        available_cash = _get_available_cash()

        current_positions = _count_equity_positions()
        slots_available = equity_cfg.max_positions - current_positions
        if slots_available <= 0:
            log.info("[equity_swing] Max positions (%d) reached.", equity_cfg.max_positions)
            return []

        sector_values = _get_sector_values()

        # Build a lookup: symbol → RankedSymbol for equity_eligible check
        ranked_lookup = {rs.symbol: rs for rs in context.ranked}

        intents: List[TradeIntent] = []
        for candidate, breakdown in ranked:
            if len(intents) >= slots_available:
                break

            # Require equity_eligible flag from composite ranking
            rs = ranked_lookup.get(candidate.symbol)
            if rs is not None and not rs.equity_eligible:
                log.debug("[equity_swing] %s skipped: equity_eligible=False", candidate.symbol)
                continue

            # Entry threshold filter (composite score in [0,1])
            if breakdown.final_score < equity_cfg.long_entry_threshold:
                continue

            # Only long entries in v1 (direction=="long" from score_symbol)
            if breakdown.direction != "long":
                continue
            if equity_cfg.long_only and breakdown.direction == "short":
                continue

            entry_price = breakdown.last_price
            atr_val = breakdown.atr14

            if not entry_price or entry_price <= 0:
                log.debug("[equity_swing] %s: no price data, skipping.", candidate.symbol)
                continue
            if not atr_val or atr_val <= 0:
                log.debug("[equity_swing] %s: no ATR data, skipping.", candidate.symbol)
                continue

            # ATR-based position sizing
            intent = _size_equity_trade(
                candidate=candidate,
                breakdown=breakdown,
                entry_price=entry_price,
                atr_val=atr_val,
                nav=nav,
                available_cash=available_cash,
                sector_values=sector_values,
                equity_cfg=equity_cfg,
                regime=context.regime,
                bot_id=self.bot_id,
            )
            if intent is None:
                continue

            # Update running available_cash to avoid over-allocating within a cycle
            if intent.quantity and intent.limit_price:
                trade_cost = intent.quantity * intent.limit_price
                available_cash = max(0.0, available_cash - trade_cost)
                sector_values[candidate.sector] = (
                    sector_values.get(candidate.sector, 0.0) + trade_cost
                )

            intents.append(intent)

        return intents

    def execute_intent(self, intent: TradeIntent, context: BotContext) -> Optional[str]:
        from execution.equity_execution import place_equity_order
        return place_equity_order(intent, context.client, context.approve)

    def run(
        self,
        mode: str = "paper",
        approve: bool = True,
        dry_run: bool = False,
        client=None,
    ) -> BotRunResult:
        cfg = get_config()
        if not cfg.bots.equity_swing.enabled:
            log.info("[equity_swing] Bot is disabled.")
            return BotRunResult(
                bot_id=self.bot_id, regime="unknown", universe_size=0,
                candidates=[], intents=[], executed=0, skipped=0,
                skip_reasons=["bot_disabled"], errors=[],
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        return super().run(mode=mode, approve=approve, dry_run=dry_run, client=client)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _get_nav() -> float:
    with get_db() as db:
        row = db.query(EquitySnapshot).order_by(EquitySnapshot.id.desc()).first()
    return row.net_liquidation if row else 0.0


def _get_available_cash() -> float:
    """Cash minus amounts reserved by pending/submitted orders (all portfolios)."""
    with get_db() as db:
        snap = db.query(EquitySnapshot).order_by(EquitySnapshot.id.desc()).first()
        if snap is None:
            return 0.0
        reserved_rows = (
            db.query(Order)
            .filter(Order.status.in_(["pending", "pending_approval", "submitted"]))
            .with_entities(Order.max_loss)
            .all()
        )
        total_reserved = sum(r.max_loss for r in reserved_rows if r.max_loss)
    return max(0.0, snap.cash - total_reserved)


def _count_equity_positions() -> int:
    from common.models import Position
    with get_db() as db:
        return (
            db.query(Position)
            .filter(Position.portfolio_id == "equity_swing")
            .count()
        )


def _get_sector_values() -> dict:
    """Sum of current equity_swing position market values by sector."""
    from common.models import Position
    from common.models import Universe
    result: dict = {}
    with get_db() as db:
        positions = (
            db.query(Position)
            .filter(Position.portfolio_id == "equity_swing")
            .all()
        )
        for pos in positions:
            # Look up sector from universe table
            uni = db.query(Universe).filter(Universe.symbol == pos.symbol).first()
            sector = uni.sector if uni else "Unknown"
            result[sector] = result.get(sector, 0.0) + abs(pos.market_value)
    return result


def _size_equity_trade(
    candidate: "Candidate",
    breakdown: "ScoreBreakdown",
    entry_price: float,
    atr_val: float,
    nav: float,
    available_cash: float,
    sector_values: dict,
    equity_cfg,
    regime: str,
    bot_id: str,
) -> Optional["TradeIntent"]:
    if entry_price <= 0:
        return None
    if atr_val <= 0:
        atr_val = entry_price * 0.02  # fallback: 2% of price as stop distance

    stop_distance = atr_val * equity_cfg.atr_stop_multiplier
    stop_price = entry_price - stop_distance
    risk_per_share = max(stop_distance, 0.01)

    risk_amount = nav * equity_cfg.risk_per_trade_pct / 100
    if risk_amount <= 0:
        return None

    shares = math.floor(risk_amount / risk_per_share)
    if shares < 1:
        log.debug(
            "[equity_swing] %s: computed 0 shares (risk=%.2f stop_dist=%.4f)",
            candidate.symbol, risk_amount, stop_distance,
        )
        return None

    # Cap to available cash
    trade_cost = shares * entry_price
    if trade_cost > available_cash:
        shares = math.floor(available_cash / entry_price)
        if shares < 1:
            log.info(
                "[equity_swing] %s: insufficient cash (need $%.0f, have $%.0f)",
                candidate.symbol, trade_cost, available_cash,
            )
            return None
        trade_cost = shares * entry_price

    # Sector concentration check
    current_sector_value = sector_values.get(candidate.sector, 0.0)
    if nav > 0:
        proposed_concentration = (current_sector_value + trade_cost) / nav
        if proposed_concentration > equity_cfg.max_sector_concentration:
            log.info(
                "[equity_swing] %s: sector %s concentration %.1f%% > limit %.1f%%",
                candidate.symbol, candidate.sector,
                proposed_concentration * 100, equity_cfg.max_sector_concentration * 100,
            )
            return None

    return TradeIntent(
        symbol=candidate.symbol,
        direction="long",
        instrument_type="equity",
        score=breakdown.final_score,
        explanation=(
            f"equity long: score={breakdown.final_score:.3f} "
            f"entry={entry_price:.2f} stop={stop_price:.2f} "
            f"qty={shares} atr={atr_val:.3f}"
        ),
        components=breakdown.components,
        regime=regime,
        bot_id=bot_id,
        max_risk_usd=round(risk_per_share * shares, 2),
        quantity=shares,
        limit_price=round(entry_price, 2),
        stop_price=round(stop_price, 2),
        atr=atr_val,
    )
