"""Tests for universe builder and contract verification."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from trader.universe import UniverseItem, verify_contract, get_verified_universe, SEED_TICKERS


# ── Helpers ────────────────────────────────────────────────────────────

def _make_db_universe(symbols: list[str]) -> list:
    rows = []
    for sym, stype, sector in SEED_TICKERS:
        if sym in symbols:
            m = MagicMock()
            m.symbol = sym
            m.type = stype
            m.sector = sector
            m.active = True
            rows.append(m)
    return rows


# ── Universe merge & dedup ─────────────────────────────────────────────

def test_seed_tickers_no_duplicates():
    syms = [s for s, _, _ in SEED_TICKERS]
    assert len(syms) == len(set(syms)), "SEED_TICKERS contains duplicates"


def test_universe_item_fields():
    item = UniverseItem(symbol="AAPL", sector="Technology", name="Apple", type="STK")
    assert item.verified is True
    assert item.conid is None
    assert item.sources == []


def test_always_etfs_present():
    always = {"SPY", "QQQ", "IWM", "DIA"}
    seed_syms = {s for s, _, _ in SEED_TICKERS}
    assert always.issubset(seed_syms), "Core always-ETFs must be in SEED_TICKERS"


def test_get_verified_universe_no_ibkr():
    """With no IBKR client, discovered tickers are skipped; core tickers returned."""
    from trader.sentiment.scoring import RecentTickerScore

    mock_rows = []
    for sym, stype, sector in [("SPY", "ETF", "Broad Market"),
                                ("AAPL", "STK", "Technology"),
                                ("QQQ", "ETF", "Technology")]:
        r = MagicMock()
        r.symbol = sym; r.type = stype; r.sector = sector; r.active = True
        mock_rows.append(r)

    discovered = [
        RecentTickerScore(symbol="XYZ", last_seen_at=datetime.now(timezone.utc),
                          latest_score=0.5, mentions_count=3)
    ]

    no_ibkr = {"verified": False, "conid": None, "primary_exchange": None,
               "reason": "no_ibkr_connection"}

    with patch("trader.universe.get_db") as mock_db_ctx, \
         patch("trader.universe.get_recent_ticker_scores", return_value=discovered), \
         patch("trader.universe.verify_contract", return_value=no_ibkr):

        mock_db = MagicMock()
        mock_db_ctx.return_value.__enter__.return_value = mock_db
        mock_db.query.return_value.filter.return_value.all.return_value = mock_rows

        items = get_verified_universe(client=None)

    syms = {i.symbol for i in items}
    assert "SPY" in syms
    assert "AAPL" in syms
    # XYZ discovered but no IBKR → not included
    assert "XYZ" not in syms


def test_get_verified_universe_with_ibkr_verifies_discovered():
    """With IBKR client, discovered tickers are verified before inclusion."""
    from trader.sentiment.scoring import RecentTickerScore

    mock_client = MagicMock()
    discovered = [
        RecentTickerScore(symbol="PLTR", last_seen_at=datetime.now(timezone.utc),
                          latest_score=0.6, mentions_count=5)
    ]
    verify_result = {"verified": True, "conid": 12345, "primary_exchange": "NYSE", "reason": "ok"}

    with patch("trader.universe.get_db") as mock_db_ctx, \
         patch("trader.universe.get_recent_ticker_scores", return_value=discovered), \
         patch("trader.universe.verify_contract", return_value=verify_result):

        mock_db = MagicMock()
        mock_db_ctx.return_value.__enter__.return_value = mock_db
        mock_db.query.return_value.filter.return_value.all.return_value = []

        items = get_verified_universe(client=mock_client)

    syms = {i.symbol for i in items}
    assert "PLTR" in syms


def test_verify_contract_no_ibkr():
    with patch("trader.universe.get_db") as mock_db_ctx, \
         patch("trader.universe.get_config") as mock_cfg:
        mock_cfg.return_value.ranking.contract_cache_hours = 24
        mock_db = MagicMock()
        mock_db_ctx.return_value.__enter__.return_value = mock_db
        # No cache entry
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = verify_contract("NEWSTOCK", client=None)

    assert result["verified"] is False
    assert result["reason"] == "no_ibkr_connection"


def test_verify_contract_rejects_otc():
    """OTC/pink-sheet exchanges are rejected."""
    mock_client = MagicMock()

    mock_detail = MagicMock()
    mock_detail.primaryExch = "PINK"
    mock_detail.contract.currency = "USD"
    mock_detail.contract.conId = 99

    mock_client.ib.reqContractDetails.return_value = [mock_detail]

    with patch("trader.universe.get_db") as mock_db_ctx:
        mock_db = MagicMock()
        mock_db_ctx.return_value.__enter__.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = verify_contract("OTCSTOCK", client=mock_client)

    assert result["verified"] is False
    assert "otc" in result["reason"].lower()


def test_verify_contract_cache_hit():
    """A fresh cache entry is returned without calling IBKR."""
    from common.time import utcnow

    cached_row = MagicMock()
    cached_row.verified = True
    cached_row.contract_conid = 42
    cached_row.primary_exchange = "NASDAQ"
    cached_row.reason = "ok"
    # checked_at = now (within cache window)
    cached_row.checked_at = utcnow().replace(tzinfo=None)

    with patch("trader.universe.get_db") as mock_db_ctx, \
         patch("trader.universe.get_config") as mock_cfg:
        mock_cfg.return_value.ranking.contract_cache_hours = 24

        mock_db = MagicMock()
        mock_db_ctx.return_value.__enter__.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = cached_row

        result = verify_contract("AAPL", client=MagicMock())

    assert result["verified"] is True
    assert result["conid"] == 42
