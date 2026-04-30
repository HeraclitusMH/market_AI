from api.v1.rankings import _normalize_ranking


def test_normalize_ranking_excludes_legacy_liquidity_weight_from_score():
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

    assert score_total == 0.642
    assert normalized["total_score"] == 0.642
    assert "liquidity" not in normalized["weights_used"]
    assert normalized["weights_used"] == {
        "sentiment": 0.6,
        "momentum_trend": 0.0,
        "risk": 0.4,
        "fundamentals": 0.0,
    }
    assert eligible is True
    assert reasons == []


def test_normalize_ranking_maps_legacy_momentum_weight_to_momentum_trend():
    components = {
        "sentiment": {"value_0_1": 0.57, "status": "ok"},
        "momentum": {"value_0_1": 0.97, "status": "ok"},
        "risk": {"value_0_1": 0.75, "status": "ok"},
        "fundamentals": {"value_0_1": None, "status": "missing"},
        "weights_used": {
            "sentiment": 0.4615,
            "momentum": 0.2308,
            "risk": 0.3077,
            "fundamentals": 0.0,
        },
    }

    normalized, score_total, _, _ = _normalize_ranking(components, 0.72, True, [])

    assert score_total == 0.7177
    assert normalized["weights_used"] == {
        "sentiment": 0.4615,
        "momentum_trend": 0.2308,
        "risk": 0.3077,
        "fundamentals": 0.0,
    }


def test_normalize_ranking_treats_neutral_empty_fundamentals_as_missing():
    components = {
        "sentiment": {"value_0_1": 0.6, "status": "ok"},
        "momentum_trend": {"value_0_1": 0.7, "status": "ok"},
        "risk": {"value_0_1": 0.8, "status": "ok"},
        "fundamentals": {
            "value_0_1": 0.5,
            "status": "neutral",
            "metrics": {
                "total_score": 50,
                "pillars": {
                    "valuation": {"metrics": {}, "missing": True},
                    "profitability": {"metrics": {}, "missing": True},
                },
            },
        },
        "weights_used": {
            "sentiment": 0.3529,
            "momentum_trend": 0.2941,
            "risk": 0.2353,
            "fundamentals": 0.1176,
        },
    }

    normalized, score_total, _, _ = _normalize_ranking(components, 0.663, True, [])

    assert normalized["fundamentals"]["value_0_1"] is None
    assert normalized["fundamentals"]["status"] == "missing"
    assert normalized["fundamentals"]["reason"] == "no_usable_fundamental_metrics"
    assert normalized["weights_used"]["fundamentals"] == 0.0
    assert score_total == 0.6867


def test_normalize_ranking_keeps_neutral_risk_with_numeric_value():
    components = {
        "sentiment": {"value_0_1": 0.6, "status": "ok"},
        "momentum_trend": {"value_0_1": 0.7, "status": "ok"},
        "risk": {"value_0_1": 0.75, "status": "neutral", "metrics": {}},
        "fundamentals": {"value_0_1": None, "status": "missing"},
        "weights_used": {
            "sentiment": 0.3529,
            "momentum_trend": 0.2941,
            "risk": 0.2353,
            "fundamentals": 0.1176,
        },
    }

    normalized, score_total, _, _ = _normalize_ranking(components, 0.659, True, [])

    assert normalized["risk"]["value_0_1"] == 0.75
    assert normalized["risk"]["status"] == "neutral"
    assert normalized["weights_used"]["risk"] == 0.2667
    assert score_total == 0.6733


def test_normalize_ranking_keeps_7factor_composite_authoritative():
    components = {
        "sentiment": {"value_0_1": 0.6, "status": "ok"},
        "momentum_trend": {"value_0_1": 0.7, "status": "ok"},
        "risk": {"value_0_1": 0.8, "status": "ok"},
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
