"""yfinance fundamental scoring."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, TypedDict

import yfinance as yf

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
    source: str


_NEGATIVE_IS_ZERO_FIELDS = {"PEEXCLXOR", "EVCUR2EBITDA"}


class FundamentalScorer:
    """Score equity fundamentals from yfinance quote data.

    The scorer maps yfinance ``Ticker.get_info()`` fields to the configured
    metric names, normalizes metrics to 0..100, computes configured pillar
    scores, and caches results per symbol.
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
        provider = getattr(raw, "provider", None) or getattr(raw, "fallback_provider", "yfinance")
        return FundamentalsConfig(
            enabled=enabled if isinstance(enabled, bool) else False,
            ttl_days=int(_number_attr(raw, "ttl_days", 7)),
            cache_ttl_hours=_number_attr(raw, "cache_ttl_hours", 24),
            provider=str(provider or "yfinance"),
            request_timeout_seconds=_number_attr(raw, "request_timeout_seconds", 15),
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
                    "No yfinance fundamental ratios available for %s; returning neutral score %.1f.",
                    normalized_symbol,
                    self.neutral_score,
                )
                result = self._neutral_result(normalized_symbol, now, list(self._configured_fields()))
            else:
                result = self._score_from_ratios(normalized_symbol, ratios, now, "yfinance")
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
        """Fetch and map yfinance fundamentals for a stock symbol."""
        provider = str(getattr(self.fundamentals_cfg, "provider", "yfinance") or "yfinance").lower()
        if provider != "yfinance":
            log.warning("Unsupported fundamentals provider %s", provider)
            return {}
        try:
            info = self._fetch_yfinance_info(symbol)
            ratios = self._parse_yfinance_info(info)
            if ratios:
                log.info("Fetched %d yfinance fundamental ratios for %s", len(ratios), symbol)
            else:
                log.warning("yfinance returned no usable fundamental ratios for %s", symbol)
            return ratios
        except Exception as exc:
            log.warning("yfinance fundamental request failed for %s: %s", symbol, exc)
            return {}

    def _fetch_yfinance_info(self, symbol: str) -> dict:
        if self.client is not None and hasattr(self.client, "get_info"):
            info = self.client.get_info(symbol)
        else:
            ticker = yf.Ticker(symbol)
            info = ticker.get_info() if hasattr(ticker, "get_info") else ticker.info
        return info if isinstance(info, dict) else {}

    def _parse_yfinance_info(self, info: dict) -> Dict[str, float]:
        candidates = {
            "PEEXCLXOR": _number(info.get("trailingPE")),
            "PRICE2BK": _number(info.get("priceToBook")),
            "EVCUR2EBITDA": _number(info.get("enterpriseToEbitda")),
            "PRICE2SALESTTM": _number(info.get("priceToSalesTrailing12Months")),
            "TTMROEPCT": _ratio_to_percent(_number(info.get("returnOnEquity"))),
            "TTMROAPCT": _ratio_to_percent(_number(info.get("returnOnAssets"))),
            "TTMGROSMGN": _ratio_to_percent(_number(info.get("grossMargins"))),
            "TTMNPMGN": _ratio_to_percent(_number(info.get("profitMargins"))),
            "REVCHNGYR": _ratio_to_percent(_number(info.get("revenueGrowth"))),
            "EPSCHNGYR": _ratio_to_percent(_number(info.get("earningsGrowth"))),
            "REVTRENDGR": _ratio_to_percent(_number(info.get("revenueGrowth"))),
            "QCURRATIO": _number(info.get("currentRatio")),
            "QQUICKRATI": _number(info.get("quickRatio")),
            "QTOTD2EQ": _percent_to_ratio(_number(info.get("debtToEquity"))),
        }
        return {
            field: float(value)
            for field, value in candidates.items()
            if field in self._configured_fields() and value is not None
        }

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
        """Compute weighted score, redistributing missing pillar weights."""
        present = {
            name: pillar
            for name, pillar in pillar_scores.items()
            if not pillar.get("missing")
        }
        if not present:
            return round(self.neutral_score, 1)

        total_weight = sum(float(p["weight"]) for p in present.values())
        if total_weight <= 0:
            return round(self.neutral_score, 1)

        total = sum(
            float(p["score"]) * (float(p["weight"]) / total_weight)
            for p in present.values()
        )
        return round(max(0.0, min(100.0, total)), 1)

    def _score_from_ratios(
        self,
        symbol: str,
        ratios: Dict[str, float],
        timestamp: datetime,
        source: str = "yfinance",
    ) -> FundamentalResult:
        pillars = {
            pillar_name: self._compute_pillar_score(pillar_name, ratios)
            for pillar_name in self.fundamentals_cfg.pillars
        }
        configured_fields = set(self._configured_fields())
        missing_fields = sorted(field for field in configured_fields if field not in ratios)
        if missing_fields:
            log.warning("Missing fundamental metrics for %s: %s", symbol, missing_fields)

        total_score = self._compute_total_score(pillars)
        if all(pillar["missing"] for pillar in pillars.values()):
            log.warning("All fundamental pillars missing for %s; returning neutral score", symbol)

        return {
            "symbol": symbol,
            "total_score": round(total_score, 1),
            "pillars": pillars,
            "missing_fields": missing_fields,
            "cached": False,
            "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
            "source": source,
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
            "source": "none",
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


def _number(value: object) -> Optional[float]:
    if not isinstance(value, (int, float)):
        return None
    return float(value)


def _ratio_to_percent(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return value * 100 if abs(value) <= 2.0 else value


def _percent_to_ratio(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return value / 100 if abs(value) > 10.0 else value
