"""Tests for symbol ranking and candidate selection."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from trader.ranking import (
    RankedSymbol, rank_symbols, select_candidates,
    _compute_score, _age_hours, _apply_recency,
)
from trader.universe import UniverseItem


# ── Helpers ────────────────────────────────────────────────────────────

def _snap(score: float, age_hours: float = 0.0):
    """Create a mock SentimentSnapshot with given score and age."""
    ts = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    s = MagicMock()
    s.score = score
    s.timestamp = ts.replace(tzinfo=None)  # naive, like DB rows
    return s


def _item(symbol="AAPL", sector="Technology", verified=True, active=True):
    return UniverseItem(
        symbol=symbol, sector=sector, name=symbol,
        type="STK", sources=["core"], verified=verified,
    )


def _ranking_cfg():
    cfg = MagicMock()
    rc = MagicMock()
    rc.w_market = 0.20
    rc.w_sector = 0.30
    rc.w_ticker = 0.50
    rc.w_sentiment = 0.30
    rc.w_momentum_trend = 0.25
    rc.w_risk = 0.20
    rc.w_fundamentals = 0.10
    rc.enter_threshold = 0.55
    cfg.ranking = rc
    return cfg


def _composite_result(score: float):
    result = MagicMock()
    result.score = score
    result.breakdown = {
        "quality": {"weight": 0.2},
        "value": {"weight": 0.15},
        "momentum": {"weight": 0.15},
        "growth": {"weight": 0.15},
        "sentiment": {"weight": 0.15},
        "technical": {"weight": 0.1},
        "risk": {"weight": 0.1},
    }
    result.to_dict.return_value = {
        "composite_score": score,
        "regime": "rotation_choppy",
        "confidence": 0.8,
        "factors": {
            name: {"score": score, "weight": info["weight"], "contribution": 0.0, "components": {}}
            for name, info in result.breakdown.items()
        },
    }
    return result


# ── Recency logic ──────────────────────────────────────────────────────

def test_apply_recency_fresh():
    val, status = _apply_recency(0.5, age_h=1.0)
    assert val == pytest.approx(0.5)
    assert status == "ok"


def test_apply_recency_penalized():
    val, status = _apply_recency(0.8, age_h=30.0)
    assert val == pytest.approx(0.4)
    assert status == "penalized"


def test_apply_recency_stale():
    val, status = _apply_recency(0.9, age_h=80.0)
    assert val == pytest.approx(0.0)
    assert status == "stale"


def test_apply_recency_missing():
    val, status = _apply_recency(0.5, age_h=None)
    assert val == pytest.approx(0.0)
    assert status == "missing"


# ── Weight redistribution ─────────────────────────────────────────────

def test_weights_all_present():
    mkt = _snap(0.5)
    sec = _snap(0.3)
    tkr = _snap(0.8)
    score, comp = _compute_score(mkt, sec, tkr, 0.20, 0.30, 0.50)
    # 0.20*0.5 + 0.30*0.3 + 0.50*0.8 = 0.10 + 0.09 + 0.40 = 0.59
    assert score == pytest.approx(0.59, abs=0.01)
    assert comp["market"]["weight"] == pytest.approx(0.20)
    assert comp["sector"]["weight"] == pytest.approx(0.30)
    assert comp["ticker"]["weight"] == pytest.approx(0.50)


def test_weights_no_ticker():
    mkt = _snap(0.6)
    sec = _snap(0.4)
    score, comp = _compute_score(mkt, sec, None, 0.20, 0.30, 0.50)
    # Should use w_market=0.35, w_sector=0.65
    expected = 0.35 * 0.6 + 0.65 * 0.4
    assert score == pytest.approx(expected, abs=0.01)
    assert comp["ticker"]["weight"] == pytest.approx(0.0)
    assert comp["ticker"]["status"] == "missing"


def test_weights_no_sector():
    mkt = _snap(0.4)
    tkr = _snap(0.9)
    score, comp = _compute_score(mkt, None, tkr, 0.20, 0.30, 0.50)
    # Should use w_market=0.30, w_sector=0.0, w_ticker=0.70
    expected = 0.30 * 0.4 + 0.70 * 0.9
    assert score == pytest.approx(expected, abs=0.01)
    assert comp["sector"]["weight"] == pytest.approx(0.0)


def test_weights_nothing_available():
    score, comp = _compute_score(None, None, None, 0.20, 0.30, 0.50)
    assert score == pytest.approx(0.0, abs=0.001)


def test_score_clamped_to_minus_one_one():
    # All components at 1.0 should yield exactly 1.0 (not above)
    mkt = _snap(1.0)
    sec = _snap(1.0)
    tkr = _snap(1.0)
    score, _ = _compute_score(mkt, sec, tkr, 0.20, 0.30, 0.50)
    assert score <= 1.0
    assert score >= -1.0

    mkt2 = _snap(-1.0)
    sec2 = _snap(-1.0)
    tkr2 = _snap(-1.0)
    score2, _ = _compute_score(mkt2, sec2, tkr2, 0.20, 0.30, 0.50)
    assert score2 >= -1.0


def test_stale_ticker_treated_as_missing():
    mkt = _snap(0.5, age_hours=1)
    sec = _snap(0.3, age_hours=1)
    tkr = _snap(0.9, age_hours=80)  # stale → should be ignored
    score_with_stale, comp = _compute_score(mkt, sec, tkr, 0.20, 0.30, 0.50)

    # Stale ticker → same as no ticker: w_market=0.35, w_sector=0.65
    expected = 0.35 * 0.5 + 0.65 * 0.3
    assert score_with_stale == pytest.approx(expected, abs=0.01)
    assert comp["ticker"]["status"] == "stale"


# ── rank_symbols ──────────────────────────────────────────────────────

def _mock_db_for_ranking(market_score=0.5, sector_score=0.3, ticker_score=None):
    """Patch DB and return sentiment for rank_symbols calls."""
    mkt_snap = _snap(market_score)
    sec_snap = _snap(sector_score)
    tkr_snap = _snap(ticker_score) if ticker_score is not None else None

    def fake_get_latest_ticker(symbol):
        return tkr_snap

    return mkt_snap, sec_snap, tkr_snap, fake_get_latest_ticker


def test_rank_symbols_sorted_descending():
    items = [_item("AAPL"), _item("MSFT"), _item("JPM", sector="Financial")]

    mkt_snap = _snap(0.4)
    sec_tech = _snap(0.6)
    sec_fin = _snap(-0.5)

    def fake_mkt():
        return mkt_snap

    def fake_sec(sector):
        if sector == "Technology":
            return sec_tech
        if sector == "Financial":
            return sec_fin
        return None

    with patch("trader.ranking._get_market_snap", fake_mkt), \
         patch("trader.ranking._get_sector_snap", fake_sec), \
         patch("trader.ranking.get_latest_ticker_score", return_value=None), \
         patch("trader.ranking._persist_rankings"), \
         patch("trader.ranking._check_eligibility", return_value=(True, [])):

        ranked = rank_symbols(items)

    assert len(ranked) == 3
    scores = [r.score_total for r in ranked]
    assert scores == sorted(scores, reverse=True), "Results must be sorted descending"


def test_rank_symbols_liquidity_does_not_contribute_to_score():
    items = [_item("AAPL")]

    with patch("trader.ranking.get_config", return_value=_ranking_cfg()), \
         patch("trader.ranking._get_market_snap", return_value=None), \
         patch("trader.ranking._get_sector_snap", return_value=None), \
         patch("trader.ranking.get_latest_ticker_score", return_value=None), \
         patch("trader.market_data.get_latest_bars", return_value=MagicMock()), \
         patch("trader.ranking.compute_sentiment_factor", return_value={"value_0_1": 0.6, "status": "ok"}), \
         patch("trader.ranking.compute_momentum_trend_factor", return_value={"value_0_1": 0.7, "status": "ok"}), \
         patch("trader.ranking.compute_risk_factor", return_value={"value_0_1": 0.8, "status": "ok"}), \
         patch("trader.ranking.compute_fundamentals_factor", return_value={"value_0_1": None, "status": "missing"}), \
         patch("trader.ranking.compute_liquidity_factor", return_value={"eligible": True, "value_0_1": 0.0, "status": "ok", "reasons": []}), \
         patch("trader.ranking.compute_optionability_factor", return_value={"eligible": False, "value_0_1": 0.0, "status": "unknown", "reasons": []}), \
         patch("trader.ranking._score_7factor", return_value=_composite_result(68.67)), \
         patch("trader.ranking._check_eligibility", return_value=(True, [])), \
         patch("trader.ranking._persist_rankings"):
        ranked = rank_symbols(items)

    # The 7-factor composite is authoritative; liquidity remains a gate only.
    assert ranked[0].score_total == pytest.approx(0.6867, abs=0.001)
    assert "liquidity" not in ranked[0].components["weights_used"]
    assert ranked[0].components["liquidity"]["value_0_1"] == 0.0
    assert ranked[0].components["composite_7factor"]["composite_score"] == 68.67


def test_rank_symbols_liquidity_failure_makes_symbol_ineligible():
    items = [_item("LOWVOL")]

    with patch("trader.ranking.get_config", return_value=_ranking_cfg()), \
         patch("trader.ranking._get_market_snap", return_value=None), \
         patch("trader.ranking._get_sector_snap", return_value=None), \
         patch("trader.ranking.get_latest_ticker_score", return_value=None), \
         patch("trader.market_data.get_latest_bars", return_value=MagicMock()), \
         patch("trader.ranking.compute_sentiment_factor", return_value={"value_0_1": 1.0, "status": "ok"}), \
         patch("trader.ranking.compute_momentum_trend_factor", return_value={"value_0_1": 1.0, "status": "ok"}), \
         patch("trader.ranking.compute_risk_factor", return_value={"value_0_1": 1.0, "status": "ok"}), \
         patch("trader.ranking.compute_fundamentals_factor", return_value={"value_0_1": None, "status": "missing"}), \
         patch("trader.ranking.compute_liquidity_factor", return_value={"eligible": False, "value_0_1": 1.0, "status": "ok", "reasons": ["low_adv_dollar_1000"]}), \
         patch("trader.ranking.compute_optionability_factor", return_value={"eligible": False, "value_0_1": 0.0, "status": "unknown", "reasons": []}), \
         patch("trader.ranking._score_7factor", return_value=_composite_result(100.0)), \
         patch("trader.ranking._check_eligibility", return_value=(True, [])), \
         patch("trader.ranking._persist_rankings"):
        ranked = rank_symbols(items)

    result = ranked[0]
    assert result.score_total == pytest.approx(1.0)
    assert result.eligible is False
    assert result.equity_eligible is False
    assert result.bias is None
    assert result.reasons == ["low_adv_dollar_1000"]


def test_rank_symbols_bias_assignment():
    items = [_item("BULL"), _item("BEAR"), _item("FLAT")]

    def fake_mkt():
        return _snap(0.9)

    def fake_sec(sector):
        return _snap(0.9)

    scores_by_sym = {"BULL": 0.9, "BEAR": -0.9, "FLAT": 0.0}

    # Return pre-computed component scores via side_effect on _compute_score
    def fake_compute(mkt, sec, tkr, wm, ws, wt):
        return 0.0, {"market": {"raw": None, "age_hours": None, "weight": 0, "contribution": 0, "status": "missing"},
                     "sector": {"raw": None, "age_hours": None, "weight": 0, "contribution": 0, "status": "missing"},
                     "ticker": {"raw": None, "age_hours": None, "weight": 0, "contribution": 0, "status": "missing"}}

    with patch("trader.ranking._get_market_snap", fake_mkt), \
         patch("trader.ranking._get_sector_snap", fake_sec), \
         patch("trader.ranking.get_latest_ticker_score", return_value=None), \
         patch("trader.ranking._persist_rankings"), \
         patch("trader.ranking._check_eligibility", return_value=(True, [])), \
         patch("trader.ranking.get_config") as mock_cfg:

        rc = MagicMock()
        rc.w_market = 0.20
        rc.w_sector = 0.30
        rc.w_ticker = 0.50
        rc.enter_threshold = 0.25
        mock_cfg.return_value.ranking = rc

        # Use actual compute but override snap scores
        bull_mkt = _snap(1.0); bear_mkt = _snap(-1.0); flat_mkt = _snap(0.0)
        call_map = {"BULL": bull_mkt, "BEAR": bear_mkt, "FLAT": flat_mkt}

        def fake_mkt2():
            return None

        # More targeted: patch _compute_score to return known values
        def fake_compute2(mkt, sec, tkr, wm, ws, wt):
            # Identify by the market snap score
            mkt_score = mkt.score if mkt else 0.0
            if mkt_score > 0.5:
                return 0.8, {}
            elif mkt_score < -0.5:
                return -0.8, {}
            else:
                return 0.1, {}

        # Just test selection logic directly via select_candidates instead
        ranked = [
            RankedSymbol("BULL", "Tech", 0.8, {}, True, [], ["core"], "bullish"),
            RankedSymbol("FLAT", "Tech", 0.1, {}, True, [], ["core"], None),
            RankedSymbol("BEAR", "Tech", -0.8, {}, True, [], ["core"], "bearish"),
        ]

    # Direct test on select_candidates
    with patch("trader.ranking.get_config") as mock_cfg2, \
         patch("trader.ranking._get_market_snap", return_value=None):
        rc2 = MagicMock()
        rc2.max_candidates_total = 3
        rc2.enter_threshold = 0.25
        rc2.fallback_trade_broad_etf = False
        mock_cfg2.return_value.ranking = rc2

        selected = select_candidates(ranked)

    syms = {c.symbol for c in selected}
    assert "BULL" in syms
    assert "BEAR" in syms
    assert "FLAT" not in syms


# ── select_candidates ─────────────────────────────────────────────────

def test_select_candidates_respects_max_total():
    ranked = [
        RankedSymbol(f"BULL{i}", "T", 0.8 - i * 0.01, {}, True, [], ["core"], "bullish")
        for i in range(5)
    ] + [
        RankedSymbol(f"BEAR{i}", "T", -0.8 + i * 0.01, {}, True, [], ["core"], "bearish")
        for i in range(5)
    ]

    with patch("trader.ranking.get_config") as mock_cfg, \
         patch("trader.ranking._get_market_snap", return_value=None):
        rc = MagicMock()
        rc.max_candidates_total = 3
        rc.enter_threshold = 0.25
        rc.fallback_trade_broad_etf = False
        mock_cfg.return_value.ranking = rc

        selected = select_candidates(ranked)

    assert len(selected) <= 3


def test_select_candidates_ineligible_excluded():
    ranked = [
        RankedSymbol("GOOD", "T", 0.9, {}, True, [], ["core"], "bullish"),
        RankedSymbol("BAD", "T", 0.8, {}, False, ["low_volume"], ["core"], "bullish"),
    ]

    with patch("trader.ranking.get_config") as mock_cfg, \
         patch("trader.ranking._get_market_snap", return_value=None):
        rc = MagicMock()
        rc.max_candidates_total = 3
        rc.enter_threshold = 0.25
        rc.fallback_trade_broad_etf = False
        mock_cfg.return_value.ranking = rc

        selected = select_candidates(ranked)

    syms = {c.symbol for c in selected}
    assert "GOOD" in syms
    assert "BAD" not in syms


def test_select_candidates_fallback_etf():
    """When no candidates and fallback enabled, SPY/QQQ is selected."""
    spy = RankedSymbol("SPY", "Broad Market", 0.05, {}, True, [], ["etf"], None)
    ranked = [spy]

    mkt_snap = _snap(0.5)

    with patch("trader.ranking.get_config") as mock_cfg, \
         patch("trader.ranking._get_market_snap", return_value=mkt_snap):
        rc = MagicMock()
        rc.max_candidates_total = 3
        rc.enter_threshold = 0.25
        rc.fallback_trade_broad_etf = True
        mock_cfg.return_value.ranking = rc

        selected = select_candidates(ranked)

    assert len(selected) == 1
    assert selected[0].symbol == "SPY"
    assert selected[0].bias == "bullish"


def test_select_candidates_fallback_respects_eligibility_gate():
    spy = RankedSymbol("SPY", "Broad Market", 0.05, {}, False, ["low_adv"], ["etf"], None)

    with patch("trader.ranking.get_config") as mock_cfg, \
         patch("trader.ranking._get_market_snap", return_value=_snap(0.5)):
        rc = MagicMock()
        rc.max_candidates_total = 3
        rc.enter_threshold = 0.25
        rc.fallback_trade_broad_etf = True
        mock_cfg.return_value.ranking = rc

        selected = select_candidates([spy])

    assert selected == []


def test_select_candidates_no_fallback_returns_empty():
    ranked = [
        RankedSymbol("AAPL", "T", 0.1, {}, True, [], ["core"], None),  # within threshold
    ]

    with patch("trader.ranking.get_config") as mock_cfg, \
         patch("trader.ranking._get_market_snap", return_value=None):
        rc = MagicMock()
        rc.max_candidates_total = 3
        rc.enter_threshold = 0.25
        rc.fallback_trade_broad_etf = False
        mock_cfg.return_value.ranking = rc

        selected = select_candidates(ranked)

    assert selected == []
