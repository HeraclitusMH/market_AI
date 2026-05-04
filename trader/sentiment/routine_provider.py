"""Sentiment provider that reads pre-computed Claude Routine output."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Dict, List, Optional

import requests

from common.logging import get_logger
from trader.sentiment.base import SentimentProvider, SentimentResult

log = get_logger(__name__)


class StaleDataError(Exception):
    """Raised when the routine output file is older than the configured threshold."""


class RoutineProvider(SentimentProvider):
    def __init__(
        self,
        *,
        source_type: str = "local",
        github_raw_url: str = "",
        local_path: str = "data/sentiment_output.json",
        max_staleness_hours: float = 8.0,
        github_token: Optional[str] = None,
    ):
        self.source_type = source_type.strip().lower()
        self.github_raw_url = github_raw_url
        self.local_path = local_path
        self.max_staleness_hours = max_staleness_hours
        self.github_token = github_token
        self._cached_data: Optional[dict] = None
        self._cache_ts: Optional[float] = None
        self._cache_ttl_seconds = 60.0

    def fetch_market_sentiment(self) -> Optional[SentimentResult]:
        data = self._load_output()
        if not data:
            return None
        market = data.get("market")
        if not isinstance(market, dict):
            log.warning("Routine sentiment output missing market section")
            return None
        return SentimentResult(
            scope="market",
            key="overall",
            score=self._score(market.get("score"), "market.overall"),
            summary=str(market.get("summary") or ""),
            sources=self._sources(data),
        )

    def fetch_sector_sentiment(self) -> List[SentimentResult]:
        data = self._load_output()
        sectors = data.get("sectors") if data else None
        if sectors is None:
            return []
        if not isinstance(sectors, dict):
            log.warning("Routine sentiment sectors section is not an object")
            return []
        results: List[SentimentResult] = []
        for sector, item in sectors.items():
            if not isinstance(item, dict):
                log.warning("Routine sentiment sector %s is not an object", sector)
                continue
            results.append(SentimentResult(
                scope="sector",
                key=str(sector),
                score=self._score(item.get("score"), f"sector.{sector}"),
                summary=str(item.get("summary") or ""),
                sources=self._sources(data),
            ))
        return results

    def fetch_ticker_sentiment(self) -> List[SentimentResult]:
        data = self._load_output()
        tickers = data.get("tickers") if data else None
        if tickers is None:
            return []
        if not isinstance(tickers, dict):
            log.warning("Routine sentiment tickers section is not an object")
            return []
        results: List[SentimentResult] = []
        for symbol, item in tickers.items():
            if not isinstance(item, dict):
                log.warning("Routine sentiment ticker %s is not an object", symbol)
                continue
            results.append(SentimentResult(
                scope="ticker",
                key=str(symbol).upper(),
                score=self._score(item.get("score"), f"ticker.{symbol}"),
                summary=str(item.get("summary") or ""),
                sources=self._sources(data),
            ))
        return results

    def _load_output(self) -> Optional[dict]:
        now = monotonic()
        if self._cache_ts is not None and now - self._cache_ts < self._cache_ttl_seconds:
            return self._cached_data

        try:
            data = self._read_github() if self.source_type == "github" else self._read_local()
            if not isinstance(data, dict):
                log.error("Routine sentiment output root must be an object")
                data = None
            elif data.get("schema_version") != 1:
                log.error("Unsupported routine sentiment schema_version: %s", data.get("schema_version"))
                data = None
            elif data.get("timestamp"):
                self._check_staleness(str(data["timestamp"]))
        except StaleDataError:
            raise
        except Exception as exc:
            log.error("Failed to load routine sentiment output: %s", exc)
            data = None

        self._cache_ts = now
        self._cached_data = data
        return data

    def _read_local(self) -> dict:
        path = Path(self.local_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            log.warning("Routine sentiment output file does not exist: %s", path)
            return {}
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _read_github(self) -> dict:
        if not self.github_raw_url:
            log.error("Routine sentiment github_raw_url is required for github source")
            return {}
        headers = {}
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
        response = requests.get(self.github_raw_url, headers=headers, timeout=20)
        if response.status_code == 403:
            retry_after = response.headers.get("Retry-After")
            suffix = f" retry_after={retry_after}" if retry_after else ""
            log.error("GitHub routine sentiment request forbidden: 403%s", suffix)
            return {}
        if response.status_code == 404:
            log.error("GitHub routine sentiment output not found: %s", self.github_raw_url)
            return {}
        response.raise_for_status()
        return response.json()

    def _check_staleness(self, timestamp_str: str) -> None:
        try:
            timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        except ValueError as exc:
            raise StaleDataError(f"routine output timestamp is invalid: {timestamp_str}") from exc
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)
        max_age = timedelta(hours=self.max_staleness_hours)
        age_hours = age.total_seconds() / 3600
        if age > max_age:
            raise StaleDataError(
                f"routine output is {age_hours:.2f} hours old; max is {self.max_staleness_hours:.2f}"
            )
        if age > max_age * 0.75:
            log.warning(
                "Routine sentiment output is approaching staleness: %.2f hours old, max %.2f",
                age_hours,
                self.max_staleness_hours,
            )

    def _score(self, value: Any, label: str) -> float:
        try:
            score = float(value)
        except (TypeError, ValueError):
            log.warning("Routine sentiment score for %s is invalid; using 0.0", label)
            return 0.0
        clamped = max(-1.0, min(1.0, score))
        if clamped != score:
            log.warning("Routine sentiment score for %s clamped from %.4f to %.4f", label, score, clamped)
        return round(clamped, 4)

    def _sources(self, data: Dict[str, Any]) -> List[Any]:
        sources = data.get("sources")
        return sources if isinstance(sources, list) else []
