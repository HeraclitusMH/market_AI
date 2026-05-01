"""OptionsSwingBot — debit spread options strategy plugin."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from common.config import get_config
from common.db import get_db
from common.logging import get_logger
from bots.base_bot import (
    BaseBot, BotContext, BotRunResult, Candidate, ScoreBreakdown, TradeIntent,
)
from trader.strategy import score_symbol, SignalIntent
from trader.ranking import select_candidates

log = get_logger(__name__)


class OptionsSwingBot(BaseBot):
    """Trades debit spreads only (bull call / bear put).

    Uses the existing options_planner + execution pipeline unchanged.
    """

    bot_id = "options_swing"
    instrument_type = "options"

    def build_candidates(self, context: BotContext) -> List[Candidate]:
        return [
            Candidate(
                symbol=item.symbol,
                sector=item.sector,
                source=item.sources[0] if item.sources else "core",
                verified=item.verified,
            )
            for item in context.universe
            if item.symbol != "SPY" and item.verified
        ]

    def score_candidate(
        self,
        candidate: Candidate,
        context: BotContext,
    ) -> Optional[ScoreBreakdown]:
        intent = score_symbol(
            candidate.symbol, candidate.sector, context.regime, context.client
        )
        if intent is None:
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
        )

    def select_trades(
        self,
        ranked: List[Tuple[Candidate, ScoreBreakdown]],
        context: BotContext,
    ) -> List[TradeIntent]:
        cfg = get_config()
        if not cfg.bots.options_swing.enabled:
            log.info("[options_swing] Bot is disabled — no trades selected.")
            return []

        # Enhanced regime: block new options entries when regime disallows them
        regime_state = getattr(context, "regime_state", None)
        if regime_state is not None and not regime_state.allows_new_options_entries:
            log.info(
                "[options_swing] Regime %s blocks new options entries",
                regime_state.level.value if hasattr(regime_state, "level") else str(regime_state),
            )
            return []

        # Use the ranking pipeline's candidate selection then filter by options_eligible
        candidates_from_ranking = select_candidates(context.ranked)
        scored_map = {c.symbol: bd for c, bd in ranked}

        intents: List[TradeIntent] = []
        for rs in candidates_from_ranking:
            if not rs.eligible or rs.bias is None:
                continue
            if not rs.options_eligible:
                log.debug(
                    "[options_swing] %s skipped: options_eligible=False (%s)",
                    rs.symbol,
                    rs.components.get("optionability", {}).get("reasons", []),
                )
                continue
            bd = scored_map.get(rs.symbol)
            if bd is None:
                continue
            direction = "long" if rs.bias == "bullish" else "short"
            intents.append(TradeIntent(
                symbol=rs.symbol,
                direction=direction,
                instrument_type="options",
                score=rs.score_total,
                explanation=f"options debit spread: {rs.bias}, score={rs.score_total:.3f}",
                components=bd.components,
                regime=context.regime,
                bot_id=self.bot_id,
            ))

        return intents[:cfg.risk.max_positions]

    def execute_exit_intent(self, exit_intent, context: BotContext) -> None:
        from common.db import get_db
        from common.models import TradeManagement
        from trader.execution import close_options_spread

        with get_db() as session:
            order = close_options_spread(
                symbol=exit_intent.symbol,
                management_id=exit_intent.management_id,
                quantity=exit_intent.quantity,
                urgency=exit_intent.urgency,
                limit_price=exit_intent.limit_price,
                client=context.client,
                session=session,
                approve=context.approve,
                exit_rule=exit_intent.exit_rule,
                exit_reason=exit_intent.exit_reason,
            )
            if order is None:
                return
            tm = (
                session.query(TradeManagement).get(exit_intent.management_id)
                if exit_intent.management_id else None
            )
            if tm is not None:
                session.delete(tm)

    def execute_intent(self, intent: TradeIntent, context: BotContext) -> Optional[str]:
        """Plan + optionally execute via the full options pipeline."""
        from trader.options_planner import plan_trade
        from trader.execution import execute_signal

        # Find the matching RankedSymbol for plan_trade
        rs = next((r for r in context.ranked if r.symbol == intent.symbol), None)
        if rs is None:
            log.warning("[options_swing] No ranked symbol for %s", intent.symbol)
            return None

        plan = plan_trade(rs, context.client)
        if plan is None:
            return None
        if plan.status == "skipped":
            log.info("[options_swing] %s plan skipped: %s", intent.symbol, plan.skip_reason)
            return None

        if context.approve:
            log.info("[options_swing] Approve mode: %s plan %d saved.", intent.symbol, plan.id)
            return f"plan_{plan.id}"

        # Not approve-mode: submit order
        signal = _plan_to_signal(plan, intent)
        return execute_signal(signal, context.client)

    def run(
        self,
        mode: str = "paper",
        approve: bool = True,
        dry_run: bool = False,
        client=None,
    ) -> BotRunResult:
        cfg = get_config()
        if not cfg.bots.options_swing.enabled:
            log.info("[options_swing] Bot is disabled.")
            return BotRunResult(
                bot_id=self.bot_id, regime="unknown", universe_size=0,
                candidates=[], intents=[], executed=0, skipped=0,
                skip_reasons=["bot_disabled"], errors=[],
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        return super().run(mode=mode, approve=approve, dry_run=dry_run, client=client)


def _plan_to_signal(plan, intent: TradeIntent) -> "SignalIntent":
    pricing = json.loads(plan.pricing_json or "{}")
    rationale = json.loads(plan.rationale_json or "{}")
    return SignalIntent(
        symbol=plan.symbol,
        direction=intent.direction,
        instrument=plan.strategy,
        score=rationale.get("score_total", intent.score),
        max_risk_usd=pricing.get("max_loss_total", 0.0),
        explanation=f"From plan id={plan.id}",
        components=rationale.get("components", intent.components),
        regime=intent.regime,
    )
