from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

from trader.scheduler import Scheduler


def test_scheduler_passes_ibkr_client_to_ranking():
    client = MagicMock()
    scheduler = Scheduler(client)
    scheduler._last_sync = datetime.now()
    scheduler._last_sentiment = datetime.now()
    scheduler._last_signal = datetime.now()
    scheduler._last_ranking = datetime.min
    scheduler._last_rebalance = datetime.now().strftime("%Y-%m-%d")

    with patch.object(scheduler, "_heartbeat"), \
         patch("trader.universe.get_verified_universe", return_value=[]), \
         patch("trader.ranking.rank_symbols", return_value=[]) as rank_symbols, \
         patch("trader.ranking.select_candidates", return_value=[]):
        scheduler.run_once()

    rank_symbols.assert_called_once_with([], client=client)
