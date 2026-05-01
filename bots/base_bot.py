"""Base bot interface and shared dataclasses for all trading bots."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple

from common.logging import get_logger

log = get_logger(__name__)


@dataclass
class Candidate:
    symbol: str
    sector: str
    source: str       # "core" | "rss_discovered" | "etf"
    verified: bool


@dataclass
class ScoreBreakdown:
    trend: float
    momentum: float
    volatility: float
    sentiment: float
    final_score: float
    direction: Optional[str]      # "long" | "short" | None
    explanations: List[str]
    components: Dict[str, Any]
    atr14: Optional[float] = None       # ATR(14) in price units — used for equity sizing
    last_price: Optional[float] = None  # latest close price — used for equity sizing


@dataclass
class TradeIntent:
    symbol: str
    direction: str          # "long" | "short"
    instrument_type: str    # "equity" | "options"
    score: float
    explanation: str
    components: Dict[str, Any]
    regime: str
    bot_id: str
    max_risk_usd: float = 0.0
    # equity-specific
    quantity: Optional[int] = None
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    atr: Optional[float] = None


@dataclass
class BotContext:
    regime: str
    universe: List[Any]       # List[UniverseItem]
    ranked: List[Any]         # List[RankedSymbol]
    client: Optional[Any]
    dry_run: bool
    approve: bool
    mode: str                 # "paper" | "live"
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    portfolio_id: Optional[str] = None
    regime_state: Optional[Any] = None  # RegimeState (enhanced regime, backward compat)


@dataclass
class BotRunResult:
    bot_id: str
    regime: str
    universe_size: int
    candidates: List[Dict]
    intents: List[Dict]
    executed: int
    skipped: int
    skip_reasons: List[str]
    errors: List[str]
    timestamp: str


class BaseBot(ABC):
    """Abstract base for all trading bots.

    Subclasses implement build_candidates / score_candidate / select_trades /
    execute_intent. run() orchestrates the shared top-level flow.
    """

    bot_id: str
    instrument_type: Literal["options", "equity"]

    @abstractmethod
    def build_candidates(self, context: BotContext) -> List[Candidate]:
        """Filter the universe down to tradeable candidates for this bot."""

    @abstractmethod
    def score_candidate(
        self,
        candidate: Candidate,
        context: BotContext,
    ) -> Optional[ScoreBreakdown]:
        """Score a single candidate. Return None to skip."""

    @abstractmethod
    def select_trades(
        self,
        ranked: List[Tuple[Candidate, ScoreBreakdown]],
        context: BotContext,
    ) -> List[TradeIntent]:
        """Convert ranked, scored candidates into trade intents."""

    @abstractmethod
    def execute_intent(
        self,
        intent: TradeIntent,
        context: BotContext,
    ) -> Optional[str]:
        """Execute or queue a single trade intent. Returns intent_id or None."""

    def execute_exit_intent(self, exit_intent: Any, context: BotContext) -> None:
        """Execute a single ExitIntent. Subclasses override for instrument-specific logic."""

    def _run_exit_phase(self, context: BotContext) -> None:
        """Evaluate and execute exit intents for all open positions."""
        from common.config import get_config
        from common.db import get_db

        cfg = get_config()
        if not cfg.exits.enabled:
            return

        try:
            from trader.exits import ExitManager
            with get_db() as session:
                mgr = ExitManager(cfg.exits, session, context.client)
                evaluations = mgr.evaluate_all_positions(context)

                exit_intents = []
                for ev in evaluations:
                    if ev.should_exit:
                        exit_intents.extend(ev.exit_intents)

                exit_intents.sort(key=lambda ei: ei.priority)

                for ei in exit_intents:
                    try:
                        if not context.dry_run:
                            self.execute_exit_intent(ei, context)
                        else:
                            log.info(
                                "[%s] DRY-RUN exit: %s rule=%s qty=%d",
                                self.bot_id, ei.symbol, ei.exit_rule, ei.quantity,
                            )
                    except Exception as exc:
                        log.error("[%s] Exit execution failed for %s: %s", self.bot_id, ei.symbol, exc)

            log.info(
                "[%s] Exit phase: %d positions checked, %d exits triggered",
                self.bot_id, len(evaluations), len(exit_intents),
            )
        except Exception as exc:
            log.error("[%s] Exit phase failed: %s", self.bot_id, exc)

    def run(
        self,
        mode: str = "paper",
        approve: bool = True,
        dry_run: bool = False,
        client=None,
    ) -> BotRunResult:
        """Run one full cycle: regime → universe → score → select → execute."""
        from trader.strategy import check_regime
        from trader.universe import get_verified_universe
        from trader.ranking import rank_symbols

        ts = datetime.now(timezone.utc).isoformat()
        skip_reasons: List[str] = []
        errors: List[str] = []
        executed = 0

        regime_state = None
        try:
            regime_result = check_regime(client)
            regime = str(regime_result)
            regime_state = regime_result
        except Exception as e:
            log.error("[%s] Regime check failed: %s", self.bot_id, e)
            regime = "risk_off"
            errors.append(f"regime_check_failed: {e}")

        try:
            universe = get_verified_universe(client)
        except Exception as e:
            log.error("[%s] Universe build failed: %s", self.bot_id, e)
            universe = []
            errors.append(f"universe_failed: {e}")

        try:
            ranked_all = rank_symbols(universe)
        except Exception as e:
            log.error("[%s] Ranking failed: %s", self.bot_id, e)
            ranked_all = []
            errors.append(f"ranking_failed: {e}")

        context = BotContext(
            regime=regime,
            universe=universe,
            ranked=ranked_all,
            client=client,
            dry_run=dry_run,
            approve=approve,
            mode=mode,
            now=datetime.now(timezone.utc),
            portfolio_id=getattr(self, "bot_id", None),
            regime_state=regime_state,
        )

        # === PHASE: EXIT MANAGEMENT (runs before new entries) ===
        self._run_exit_phase(context)

        candidates = self.build_candidates(context)
        log.info(
            "[%s] %d candidates from universe of %d (regime=%s)",
            self.bot_id, len(candidates), len(universe), regime,
        )

        scored: List[Tuple[Candidate, ScoreBreakdown]] = []
        for cand in candidates:
            try:
                breakdown = self.score_candidate(cand, context)
                if breakdown is not None:
                    scored.append((cand, breakdown))
                else:
                    skip_reasons.append(f"{cand.symbol}:score_none")
            except Exception as e:
                log.warning("[%s] Scoring failed for %s: %s", self.bot_id, cand.symbol, e)
                errors.append(f"score_{cand.symbol}: {e}")

        scored.sort(key=lambda x: x[1].final_score, reverse=True)
        log.info("[%s] %d / %d candidates scored", self.bot_id, len(scored), len(candidates))

        intents = self.select_trades(scored, context)
        log.info("[%s] %d trade intents selected", self.bot_id, len(intents))

        for intent in intents:
            if dry_run:
                log.info(
                    "[%s] DRY-RUN: would %s %s qty=%s lim=%.2f",
                    self.bot_id, intent.direction, intent.symbol,
                    intent.quantity, intent.limit_price or 0.0,
                )
                executed += 1
                continue
            try:
                result = self.execute_intent(intent, context)
                if result:
                    executed += 1
                else:
                    skip_reasons.append(f"{intent.symbol}:exec_skipped")
            except Exception as e:
                log.error("[%s] Execution failed for %s: %s", self.bot_id, intent.symbol, e)
                errors.append(f"exec_{intent.symbol}: {e}")

        log.info(
            "[%s] Cycle done: regime=%s universe=%d intents=%d executed=%d "
            "skipped=%d errors=%d",
            self.bot_id, regime, len(universe), len(intents),
            executed, len(skip_reasons), len(errors),
        )

        return BotRunResult(
            bot_id=self.bot_id,
            regime=regime,
            universe_size=len(universe),
            candidates=[
                {"symbol": c.symbol, "sector": c.sector, "score": bd.final_score}
                for c, bd in scored
            ],
            intents=[
                {
                    "symbol": i.symbol,
                    "direction": i.direction,
                    "score": i.score,
                    "qty": i.quantity,
                    "limit_price": i.limit_price,
                }
                for i in intents
            ],
            executed=executed,
            skipped=len(skip_reasons),
            skip_reasons=skip_reasons,
            errors=errors,
            timestamp=ts,
        )
