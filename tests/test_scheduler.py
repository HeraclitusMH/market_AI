from __future__ import annotations

from datetime import datetime, timedelta
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


def test_scheduler_detects_settled_routine_file_change(tmp_path):
    path = tmp_path / "sentiment_output.json"
    path.write_text('{"schema_version": 1}', encoding="utf-8")

    scheduler = Scheduler(MagicMock())
    scheduler.cfg.sentiment.provider = "claude_routine"
    scheduler.cfg.sentiment.routine.source_type = "local"
    scheduler.cfg.sentiment.routine.local_path = str(path)
    scheduler.cfg.sentiment.routine.watch_local_file = True
    scheduler.cfg.sentiment.routine.watch_debounce_seconds = 0
    scheduler._routine_file_signature = scheduler._get_routine_file_signature()

    path.write_text('{"schema_version": 1, "changed": true}', encoding="utf-8")

    assert scheduler._routine_file_refresh_due() is False
    assert scheduler._routine_file_refresh_due() is True


def test_scheduler_routine_file_change_refreshes_sentiment_and_ranking(tmp_path):
    client = MagicMock()
    path = tmp_path / "sentiment_output.json"
    path.write_text('{"schema_version": 1}', encoding="utf-8")

    scheduler = Scheduler(client)
    scheduler.cfg.sentiment.provider = "claude_routine"
    scheduler.cfg.sentiment.routine.source_type = "local"
    scheduler.cfg.sentiment.routine.local_path = str(path)
    scheduler.cfg.sentiment.routine.watch_local_file = True
    scheduler.cfg.sentiment.routine.watch_debounce_seconds = 0
    scheduler._routine_file_signature = scheduler._get_routine_file_signature()

    path.write_text('{"schema_version": 1, "changed": true}', encoding="utf-8")
    scheduler._routine_pending_signature = scheduler._get_routine_file_signature()
    scheduler._routine_pending_since = datetime.now() - timedelta(seconds=1)

    with patch.object(scheduler, "_heartbeat"), \
         patch.object(scheduler, "_should_sync", return_value=False), \
         patch.object(scheduler, "_should_refresh_fundamentals", return_value=False), \
         patch.object(scheduler, "_should_refresh_sentiment", return_value=False), \
         patch.object(scheduler, "_should_rank", return_value=False), \
         patch.object(scheduler, "_should_eval_signals", return_value=False), \
         patch.object(scheduler, "_should_rebalance", return_value=False), \
         patch("trader.sentiment.factory.refresh_and_store", return_value={
             "provider": "claude_routine",
             "status": "success",
             "snapshots_written": 3,
         }) as refresh_and_store, \
         patch("trader.universe.get_verified_universe", return_value=[]) as get_universe, \
         patch("trader.ranking.rank_symbols", return_value=[]) as rank_symbols, \
         patch("trader.ranking.select_candidates", return_value=[]):
        scheduler.run_once()

    refresh_and_store.assert_called_once()
    get_universe.assert_called_once_with(client)
    rank_symbols.assert_called_once_with([], client=client)
