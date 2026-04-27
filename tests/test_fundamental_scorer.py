from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from common.config import AppConfig
from trader.fundamental_scorer import FundamentalScorer


@pytest.fixture(autouse=True)
def _clear_fundamental_cache():
    FundamentalScorer._shared_cache.clear()
    yield
    FundamentalScorer._shared_cache.clear()


SAMPLE_YFINANCE_INFO = {
    "trailingPE": 20,
    "priceToBook": 2.5,
    "enterpriseToEbitda": 12.5,
    "priceToSalesTrailing12Months": 5,
    "returnOnEquity": 0.18,
    "returnOnAssets": 0.10,
    "grossMargins": 0.40,
    "profitMargins": 0.15,
    "revenueGrowth": 0.10,
    "earningsGrowth": 0.20,
    "currentRatio": 2,
    "quickRatio": 1.4,
    "debtToEquity": 50,
}


def _cfg(**fundamental_overrides):
    fundamentals = {"enabled": True, **fundamental_overrides}
    return AppConfig(db={"path": ":memory:"}, fundamentals=fundamentals)


def test_parse_yfinance_info_maps_configured_metrics():
    scorer = FundamentalScorer(cfg=_cfg())

    ratios = scorer._parse_yfinance_info(SAMPLE_YFINANCE_INFO)

    assert ratios == {
        "PEEXCLXOR": 20.0,
        "PRICE2BK": 2.5,
        "EVCUR2EBITDA": 12.5,
        "PRICE2SALESTTM": 5.0,
        "TTMROEPCT": 18.0,
        "TTMROAPCT": 10.0,
        "TTMGROSMGN": 40.0,
        "TTMNPMGN": 15.0,
        "REVCHNGYR": 10.0,
        "EPSCHNGYR": 20.0,
        "REVTRENDGR": 10.0,
        "QCURRATIO": 2.0,
        "QQUICKRATI": 1.4,
        "QTOTD2EQ": 0.5,
    }


def test_normalize_clamps_and_handles_edges():
    scorer = FundamentalScorer(cfg=_cfg())

    assert scorer._normalize(5, worst=40, best=5) == 100
    assert scorer._normalize(40, worst=40, best=5) == 0
    assert scorer._normalize(100, worst=40, best=5) == 0
    assert scorer._normalize(None, worst=0, best=1) is None
    assert scorer._normalize(10, worst=1, best=1) == 50


def test_negative_nonsensical_valuation_metric_scores_zero():
    scorer = FundamentalScorer(cfg=_cfg())

    pillar = scorer._compute_pillar_score("valuation", {"PEEXCLXOR": -2})

    assert pillar["metrics"]["PEEXCLXOR"]["normalized"] == 0.0
    assert pillar["score"] == 0.0


def test_pillar_missing_metrics_uses_neutral_score():
    scorer = FundamentalScorer(cfg=_cfg())

    pillar = scorer._compute_pillar_score("growth", {})

    assert pillar["score"] == 50
    assert pillar["missing"] is True


def test_get_score_computes_breakdown_and_caches():
    client = MagicMock()
    client.get_info.return_value = SAMPLE_YFINANCE_INFO
    scorer = FundamentalScorer(cfg=_cfg(), client=client)

    first = scorer.get_score("aapl")
    second = scorer.get_score("AAPL")

    assert first["symbol"] == "AAPL"
    assert first["total_score"] == pytest.approx(63.0, abs=0.1)
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["total_score"] == first["total_score"]
    assert first["source"] == "yfinance"
    assert client.get_info.call_count == 1


def test_cache_expiry_fetches_again():
    client = MagicMock()
    client.get_info.return_value = SAMPLE_YFINANCE_INFO
    scorer = FundamentalScorer(cfg=_cfg(cache_ttl_hours=1), client=client)

    scorer.get_score("AAPL")
    scorer._cache["AAPL"]["timestamp_dt"] = datetime.now(timezone.utc) - timedelta(hours=2)
    scorer.get_score("AAPL")

    assert client.get_info.call_count == 2


def test_empty_response_returns_neutral_score():
    client = MagicMock()
    client.get_info.return_value = {}
    scorer = FundamentalScorer(cfg=_cfg(), client=client)

    result = scorer.get_score("AAPL")

    assert result["total_score"] == 50
    assert result["missing_fields"]
    assert all(pillar["score"] == 50 for pillar in result["pillars"].values())
    assert result["source"] == "none"


def test_empty_response_is_not_cached_so_next_run_retries():
    client = MagicMock()
    client.get_info.side_effect = [{}, SAMPLE_YFINANCE_INFO]
    scorer = FundamentalScorer(cfg=_cfg(), client=client)

    first = scorer.get_score("AAPL")
    second = scorer.get_score("AAPL")

    assert first["source"] == "none"
    assert second["source"] == "yfinance"
    assert client.get_info.call_count == 2


def test_get_score_uses_injected_yfinance_client():
    client = MagicMock()
    client.get_info.return_value = {"trailingPE": 20, "returnOnEquity": 0.18}
    scorer = FundamentalScorer(cfg=_cfg(), client=client)

    result = scorer.get_score("AMZN")

    assert result["source"] == "yfinance"
    assert result["total_score"] != 50
    assert result["pillars"]["valuation"]["metrics"]["PEEXCLXOR"]["raw"] == 20
    assert result["pillars"]["profitability"]["metrics"]["TTMROEPCT"]["raw"] == 18


def test_parse_yfinance_info_maps_realistic_yfinance_fields():
    scorer = FundamentalScorer(cfg=_cfg())
    info = {
        "trailingPE": 36.7,
        "priceToSalesTrailing12Months": 3.96,
        "priceToBook": 6.89,
        "enterpriseToEbitda": 19.83,
        "returnOnEquity": 0.223,
        "returnOnAssets": 0.069,
        "grossMargins": 0.503,
        "profitMargins": 0.108,
        "revenueGrowth": 0.136,
        "earningsGrowth": 0.05,
        "currentRatio": 1.05,
        "quickRatio": 0.84,
        "debtToEquity": 43.4,
    }

    ratios = scorer._parse_yfinance_info(info)

    assert ratios["PEEXCLXOR"] == 36.7
    assert ratios["PRICE2BK"] == 6.89
    assert ratios["EVCUR2EBITDA"] == 19.83
    assert ratios["PRICE2SALESTTM"] == 3.96
    assert ratios["TTMROEPCT"] == pytest.approx(22.3)
    assert ratios["TTMROAPCT"] == pytest.approx(6.9)
    assert ratios["TTMGROSMGN"] == pytest.approx(50.3)
    assert ratios["TTMNPMGN"] == pytest.approx(10.8)
    assert ratios["REVCHNGYR"] == pytest.approx(13.6)
    assert ratios["EPSCHNGYR"] == pytest.approx(5.0)
    assert ratios["REVTRENDGR"] == pytest.approx(13.6)
    assert ratios["QCURRATIO"] == 1.05
    assert ratios["QQUICKRATI"] == 0.84
    assert ratios["QTOTD2EQ"] == pytest.approx(0.434)


def test_total_score_redistributes_missing_pillar_weights():
    scorer = FundamentalScorer(cfg=_cfg())
    pillars = {
        "valuation": {"score": 80, "weight": 0.25, "missing": False},
        "profitability": {"score": 50, "weight": 0.30, "missing": True},
        "growth": {"score": 60, "weight": 0.25, "missing": False},
        "financial_health": {"score": 50, "weight": 0.20, "missing": True},
    }

    score = scorer._compute_total_score(pillars)

    assert score == pytest.approx(70.0, abs=0.1)
