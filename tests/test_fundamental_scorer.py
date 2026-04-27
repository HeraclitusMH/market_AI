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


SAMPLE_RATIOS_XML = """
<ReportRatios>
  <Ratios>
    <Group Name="Valuation">
      <Ratio FieldName="PEEXCLXOR">20</Ratio>
      <Ratio FieldName="PRICE2BK">2.5</Ratio>
      <Ratio FieldName="EVCUR2EBITDA">12.5</Ratio>
      <Ratio FieldName="PRICE2SALESTTM">5</Ratio>
    </Group>
    <Group Name="Profitability">
      <Ratio FieldName="TTMROEPCT">18</Ratio>
      <Ratio FieldName="TTMROAPCT">10</Ratio>
      <Ratio FieldName="TTMGROSMGN">40</Ratio>
      <Ratio FieldName="TTMNPMGN">15</Ratio>
    </Group>
    <Group Name="Growth">
      <Ratio FieldName="REVCHNGYR">10</Ratio>
      <Ratio FieldName="EPSCHNGYR">20</Ratio>
      <Ratio FieldName="REVTRENDGR">5</Ratio>
    </Group>
    <Group Name="Financial Health">
      <Ratio FieldName="QCURRATIO">2</Ratio>
      <Ratio FieldName="QQUICKRATI">1.4</Ratio>
      <Ratio FieldName="QTOTD2EQ">0.5</Ratio>
    </Group>
  </Ratios>
</ReportRatios>
"""


def _cfg(**fundamental_overrides):
    fundamentals = {"enabled": True, **fundamental_overrides}
    return AppConfig(db={"path": ":memory:"}, fundamentals=fundamentals)


def test_parse_xml_extracts_numeric_fieldname_ratios():
    scorer = FundamentalScorer(cfg=_cfg())
    xml = """
    <ReportRatios>
      <Ratio FieldName="PEEXCLXOR">18.5</Ratio>
      <Ratio FieldName="PRICE2BK"></Ratio>
      <Ratio FieldName="BADTEXT">n/a</Ratio>
      <Ratio>10</Ratio>
      <Ratio FieldName="QCURRATIO">1,234.5</Ratio>
    </ReportRatios>
    """

    ratios = scorer._parse_xml(xml)

    assert ratios == {"PEEXCLXOR": 18.5, "QCURRATIO": 1234.5}


def test_parse_xml_malformed_returns_empty():
    scorer = FundamentalScorer(cfg=_cfg())

    assert scorer._parse_xml("<ReportRatios>") == {}


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
    client.fundamental_data.return_value = SAMPLE_RATIOS_XML
    scorer = FundamentalScorer(cfg=_cfg(), client=client)

    first = scorer.get_score("aapl")
    second = scorer.get_score("AAPL")

    assert first["symbol"] == "AAPL"
    assert first["total_score"] == pytest.approx(61.6, abs=0.1)
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["total_score"] == first["total_score"]
    assert client.fundamental_data.call_count == 1


def test_cache_expiry_fetches_again():
    client = MagicMock()
    client.fundamental_data.return_value = SAMPLE_RATIOS_XML
    scorer = FundamentalScorer(cfg=_cfg(cache_ttl_hours=1), client=client)

    scorer.get_score("AAPL")
    scorer._cache["AAPL"]["timestamp_dt"] = datetime.now(timezone.utc) - timedelta(hours=2)
    scorer.get_score("AAPL")

    assert client.fundamental_data.call_count == 2


def test_empty_response_returns_neutral_score():
    client = MagicMock()
    client.fundamental_data.return_value = ""
    scorer = FundamentalScorer(cfg=_cfg(), client=client)

    result = scorer.get_score("AAPL")

    assert result["total_score"] == 50
    assert result["missing_fields"]
    assert all(pillar["score"] == 50 for pillar in result["pillars"].values())
