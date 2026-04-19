"""Bot plugin package — OptionsSwingBot and EquitySwingBot."""
from bots.base_bot import BaseBot, BotContext, BotRunResult, Candidate, ScoreBreakdown, TradeIntent
from bots.options_swing_bot import OptionsSwingBot
from bots.equity_swing_bot import EquitySwingBot

__all__ = [
    "BaseBot",
    "BotContext",
    "BotRunResult",
    "Candidate",
    "ScoreBreakdown",
    "TradeIntent",
    "OptionsSwingBot",
    "EquitySwingBot",
]
