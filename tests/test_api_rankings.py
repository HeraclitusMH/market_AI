from api.v1.rankings import _normalize_ranking


def test_normalize_ranking_marks_missing_scores_ineligible():
    components = {
        "sentiment": {"value_0_1": 0.57, "status": "ok"},
        "risk": {"value_0_1": 0.75, "status": "ok"},
        "liquidity": {"value_0_1": 1.0, "status": "ok", "eligible": True},
        "fundamentals": {"value_0_1": None, "status": "missing"},
        "weights_used": {
            "sentiment": 0.4615,
            "risk": 0.3077,
            "liquidity": 0.2308,
            "fundamentals": 0.0,
        },
    }

    normalized, score_total, eligible, reasons = _normalize_ranking(
        components, 0.72, True, []
    )

    assert score_total == 0.72
    assert "total_score" not in normalized
    assert normalized["weights_used"]["liquidity"] == 0.2308
    assert eligible is False
    assert reasons == ["missing_score_momentum_trend", "missing_score_fundamentals"]


def test_normalize_ranking_keeps_7factor_composite_authoritative():
    components = {
        "sentiment": {"value_0_1": 0.6, "status": "ok"},
        "momentum_trend": {"value_0_1": 0.7, "status": "ok"},
        "risk": {"value_0_1": 0.8, "status": "ok"},
        "liquidity": {"value_0_1": 1.0, "status": "ok", "eligible": True},
        "fundamentals": {"value_0_1": 0.5, "status": "ok"},
        "weights_used": {
            "sentiment": 0.3,
            "momentum_trend": 0.25,
            "risk": 0.2,
            "fundamentals": 0.1,
        },
        "composite_7factor": {
            "composite_score": 73.25,
            "regime": "rotation_choppy",
            "confidence": 0.88,
            "factors": {
                "quality": {"score": 70, "weight": 0.2, "contribution": 14, "components": {}},
                "value": {"score": 65, "weight": 0.15, "contribution": 9.75, "components": {}},
                "momentum": {"score": 80, "weight": 0.1, "contribution": 8, "components": {}},
                "growth": {"score": 60, "weight": 0.15, "contribution": 9, "components": {}},
                "sentiment": {"score": 75, "weight": 0.2, "contribution": 15, "components": {}},
                "technical": {"score": 85, "weight": 0.15, "contribution": 12.75, "components": {}},
                "risk": {"score": 35, "weight": 0.05, "contribution": -1.75, "components": {}},
            },
        },
    }

    normalized, score_total, eligible, reasons = _normalize_ranking(
        components, 0.62, True, []
    )

    assert score_total == 0.7325
    assert normalized["total_score"] == 0.7325
    assert normalized["weights_used"] == {
        "quality": 0.2,
        "value": 0.15,
        "momentum": 0.1,
        "growth": 0.15,
        "sentiment": 0.2,
        "technical": 0.15,
        "risk": 0.05,
    }
    assert eligible is True
    assert reasons == []


def test_normalize_ranking_still_applies_liquidity_gate_without_composite():
    components = {
        "liquidity": {"eligible": False, "reasons": ["low_adv_dollar_1000"]},
    }

    _, score_total, eligible, reasons = _normalize_ranking(components, 0.42, True, [])

    assert score_total == 0.42
    assert eligible is False
    assert reasons == [
        "low_adv_dollar_1000",
        "missing_score_sentiment",
        "missing_score_momentum_trend",
        "missing_score_risk",
        "missing_score_fundamentals",
        "missing_score_liquidity",
    ]
