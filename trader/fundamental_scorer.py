"""IBKR ReportRatios fundamental scoring."""
from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, TypedDict

from ib_insync import Stock

from common.config import FundamentalsConfig, get_config
from common.logging import get_logger

log = get_logger(__name__)


class FundamentalResult(TypedDict):
    """Structured fundamental score result."""

    symbol: str
    total_score: float
    pillars: Dict[str, dict]
    missing_fields: list[str]
    cached: bool
    timestamp: str


_NEGATIVE_IS_ZERO_FIELDS = {"PEEXCLXOR", "EVCUR2EBITDA"}


class FundamentalScorer:
    """Score equity fundamentals from IBKR ``ReportRatios`` XML.

    The scorer fetches ratio XML, parses numeric ``Ratio`` fields, normalizes
    configured metrics to 0..100, computes configured pillar scores, and caches
    results per symbol to avoid repeated IBKR fundamental data requests.
    """

    _shared_cache: dict[str, dict] = {}

    def __init__(self, cfg=None, client=None) -> None:
        self.cfg = cfg or get_config()
        self.fundamentals_cfg = self._resolve_fundamentals_cfg(self.cfg)
        self.client = client
        self.cache_ttl = timedelta(
            hours=max(float(getattr(self.fundamentals_cfg, "cache_ttl_hours", 24) or 24), 0.0)
        )
        self.neutral_score = float(getattr(self.fundamentals_cfg, "neutral_score", 50) or 50)
        self._cache = self._shared_cache
        self._validate_pillar_weights()

    def _resolve_fundamentals_cfg(self, cfg) -> FundamentalsConfig:
        """Return a complete fundamentals config, filling old partial configs."""
        raw = getattr(cfg, "fundamentals", None)
        if isinstance(getattr(raw, "pillars", None), dict) and isinstance(
            getattr(raw, "metric_bounds", None), dict
        ):
            return raw
        enabled = getattr(raw, "enabled", False)
        return FundamentalsConfig(
            enabled=enabled if isinstance(enabled, bool) else False,
            ttl_days=int(_number_attr(raw, "ttl_days", 7)),
            cache_ttl_hours=_number_attr(raw, "cache_ttl_hours", 24),
            neutral_score=_number_attr(raw, "neutral_score", 50),
            min_coverage=_number_attr(raw, "min_coverage", 0.2),
        )

    def get_score(self, symbol: str) -> FundamentalResult:
        """Return the 0..100 fundamental score and full scoring breakdown."""
        normalized_symbol = symbol.strip().upper()
        now = datetime.now(timezone.utc)

        cached = self._cache.get(normalized_symbol)
        if cached and now - cached["timestamp_dt"] <= self.cache_ttl:
            result = copy.deepcopy(cached["result"])
            result["cached"] = True
            log.info(
                "Fundamental score for %s: %.1f (cache hit)",
                normalized_symbol,
                result["total_score"],
            )
            return result

        try:
            ratios = self._fetch_ratios(normalized_symbol)
            if not ratios:
                log.warning(
                    "No fundamental ratios available for %s; returning neutral score %.1f. "
                    "IBKR Reuters/Refinitiv entitlement may be missing.",
                    normalized_symbol,
                    self.neutral_score,
                )
                result = self._neutral_result(normalized_symbol, now, list(self._configured_fields()))
            else:
                result = self._score_from_ratios(normalized_symbol, ratios, now)
        except Exception as exc:
            log.exception("Unexpected fundamental scoring failure for %s", normalized_symbol)
            result = self._neutral_result(normalized_symbol, now, list(self._configured_fields()))
            result["pillars"]["_error"] = {"reason": str(exc)}

        self._cache[normalized_symbol] = {
            "ratios": ratios if "ratios" in locals() else {},
            "result": copy.deepcopy(result),
            "timestamp_dt": now,
        }
        log.info(
            "Fundamental score for %s: %.1f (cache miss)",
            normalized_symbol,
            result["total_score"],
        )
        log.debug("Fundamental score breakdown for %s: %s", normalized_symbol, result)
        return result

    def _fetch_ratios(self, symbol: str) -> Dict[str, float]:
        """Fetch and parse IBKR ``ReportRatios`` XML for a stock symbol."""
        if self.client is None:
            from trader.ibkr_client import get_ibkr_client

            self.client = get_ibkr_client()

        try:
            contract = Stock(symbol, "SMART", "USD")
            if hasattr(self.client, "fundamental_data"):
                xml_text = self.client.fundamental_data(contract, report_type="ReportRatios")
            else:
                if hasattr(self.client, "ensure_connected"):
                    self.client.ensure_connected()
                ib = getattr(self.client, "ib", self.client)
                qualified = ib.qualifyContracts(contract)
                contract = qualified[0] if qualified else contract
                xml_text = ib.reqFundamentalData(contract, "ReportRatios", [])
        except Exception as exc:
            log.warning("IBKR fundamental data request failed for %s: %s", symbol, exc)
            return {}

        if not xml_text or not str(xml_text).strip():
            log.warning("Empty IBKR fundamental XML for %s", symbol)
            return {}
        return self._parse_xml(str(xml_text))

    def _parse_xml(self, xml_string: str) -> Dict[str, float]:
        """Parse ``ReportRatios`` XML into ``FieldName -> numeric value``."""
        try:
            root = ET.fromstring(xml_string)
            ratios: Dict[str, float] = {}
            for ratio in root.iter("Ratio"):
                field_name = ratio.attrib.get("FieldName")
                if not field_name:
                    continue
                text = (ratio.text or "").strip()
                if not text:
                    continue
                try:
                    ratios[field_name] = float(text.replace(",", ""))
                except ValueError:
                    continue
            return ratios
        except Exception as exc:
            log.warning("Failed to parse IBKR ReportRatios XML: %s", exc)
            return {}

    def _normalize(self, value: Optional[float], worst: float, best: float) -> Optional[float]:
        """Normalize a raw metric to a clamped 0..100 score."""
        if value is None:
            return None
        if best == worst:
            return self.neutral_score
        normalized = (value - worst) / (best - worst) * 100
        return round(max(0.0, min(100.0, normalized)), 1)

    def _compute_pillar_score(self, pillar_name: str, ratios: Dict[str, float]) -> dict:
        """Compute a configured pillar score and metric breakdown."""
        pillar_cfg = self.fundamentals_cfg.pillars[pillar_name]
        metric_bounds = self.fundamentals_cfg.metric_bounds
        metrics: dict[str, dict[str, float]] = {}

        for field_name in pillar_cfg.metrics:
            if field_name not in ratios or field_name not in metric_bounds:
                continue
            raw = ratios[field_name]
            bounds = metric_bounds[field_name]
            if field_name in _NEGATIVE_IS_ZERO_FIELDS and raw < 0:
                normalized = 0.0
            else:
                normalized = self._normalize(raw, float(bounds.worst), float(bounds.best))
            if normalized is None:
                continue
            metrics[field_name] = {"raw": raw, "normalized": normalized}

        if not metrics:
            log.warning("No available fundamental metrics for pillar %s", pillar_name)
            score = self.neutral_score
            missing = True
        else:
            score = round(sum(m["normalized"] for m in metrics.values()) / len(metrics), 1)
            missing = False

        return {
            "score": score,
            "weight": float(pillar_cfg.weight),
            "metrics": metrics,
            "missing": missing,
        }

    def _compute_total_score(self, pillar_scores: Dict[str, dict]) -> float:
        """Compute the configured weighted total score from pillar scores."""
        total = sum(float(p["score"]) * float(p["weight"]) for p in pillar_scores.values())
        return round(max(0.0, min(100.0, total)), 1)

    def _score_from_ratios(
        self,
        symbol: str,
        ratios: Dict[str, float],
        timestamp: datetime,
    ) -> FundamentalResult:
        pillars = {
            pillar_name: self._compute_pillar_score(pillar_name, ratios)
            for pillar_name in self.fundamentals_cfg.pillars
        }
        configured_fields = set(self._configured_fields())
        missing_fields = sorted(field for field in configured_fields if field not in ratios)
        if missing_fields:
            log.warning("Missing fundamental metrics for %s: %s", symbol, missing_fields)

        all_pillars_missing = all(pillar["missing"] for pillar in pillars.values())
        total_score = self.neutral_score if all_pillars_missing else self._compute_total_score(pillars)
        if all_pillars_missing:
            log.warning("All fundamental pillars missing for %s; returning neutral score", symbol)

        return {
            "symbol": symbol,
            "total_score": round(total_score, 1),
            "pillars": pillars,
            "missing_fields": missing_fields,
            "cached": False,
            "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        }

    def _neutral_result(
        self,
        symbol: str,
        timestamp: datetime,
        missing_fields: list[str],
    ) -> FundamentalResult:
        pillars = {
            pillar_name: {
                "score": round(self.neutral_score, 1),
                "weight": float(pillar_cfg.weight),
                "metrics": {},
                "missing": True,
            }
            for pillar_name, pillar_cfg in self.fundamentals_cfg.pillars.items()
        }
        return {
            "symbol": symbol,
            "total_score": round(self.neutral_score, 1),
            "pillars": pillars,
            "missing_fields": sorted(missing_fields),
            "cached": False,
            "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        }

    def _configured_fields(self) -> set[str]:
        fields: set[str] = set()
        for pillar_cfg in self.fundamentals_cfg.pillars.values():
            fields.update(pillar_cfg.metrics)
        return fields

    def _validate_pillar_weights(self) -> None:
        total_weight = sum(float(pillar.weight) for pillar in self.fundamentals_cfg.pillars.values())
        if abs(total_weight - 1.0) > 0.0001:
            raise ValueError(f"fundamentals pillar weights must sum to 1.0, got {total_weight:.4f}")


def _number_attr(obj, name: str, default: float) -> float:
    value = getattr(obj, name, default)
    if not isinstance(value, (int, float)):
        return float(default)
    return float(value)
