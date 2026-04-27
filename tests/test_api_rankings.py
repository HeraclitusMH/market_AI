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
