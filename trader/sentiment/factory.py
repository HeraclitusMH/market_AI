"""Provider factory and the unified refresh entrypoint."""
from __future__ import annotations

import json
import threading
from typing import Optional

from common.config import AppConfig, get_config
from common.db import get_db
from common.logging import get_logger
from common.models import EventLog, SentimentSnapshot
from common.time import utcnow

from trader.sentiment.base import SentimentProvider
from trader.sentiment.rss_provider import RSSProvider
from trader.sentiment.mock_provider import MockProvider

log = get_logger(__name__)

_REFRESH_LOCK = threading.Lock()


def get_active_provider_name(cfg: Optional[AppConfig] = None) -> str:
    cfg = cfg or get_config()
    return cfg.sentiment.provider


def build_provider(cfg: Optional[AppConfig] = None, *, use_mock: bool = False) -> SentimentProvider:
    """Build the provider selected in config.

    Lazy-imports the Claude provider so a missing ANTHROPIC_API_KEY never
    prevents the RSS path from working.
    """
    if use_mock:
        return MockProvider()
    cfg = cfg or get_config()
    name = cfg.sentiment.provider
    if name == "claude_llm":
        from trader.sentiment.claude_provider import ClaudeLlmSentimentProvider
        return ClaudeLlmSentimentProvider(
            claude_cfg=cfg.sentiment.claude,
            rss_cfg=cfg.sentiment.rss,
        )
    # default / rss_lexicon
    return RSSProvider(feeds=cfg.sentiment.rss.feeds)


def refresh_and_store(provider: Optional[SentimentProvider] = None) -> dict:
    """Run one sentiment refresh. Serialised by a module-level lock.

    Returns a dict summary suitable for logging / telemetry. Never raises on
    normal failure paths — provider errors are logged and an empty result is
    returned so the scheduler keeps running.
    """
    cfg = get_config()
    if not _REFRESH_LOCK.acquire(blocking=False):
        log.info("Sentiment refresh already in progress — skipping this tick.")
        return {"status": "skipped", "reason": "already_running"}

    try:
        provider = provider or build_provider(cfg)

        # Claude provider exposes a richer run() that already handles budget /
        # dedup / logging / aggregation. Detect and use it.
        run_fn = getattr(provider, "run", None)
        if callable(run_fn):
            run = run_fn()
            summary = {
                "status": run.status,
                "reason": run.reason,
                "provider": cfg.sentiment.provider,
                "model": getattr(run, "model", ""),
                "items_sent": getattr(run, "items_sent", 0),
                "items_valid": getattr(run, "items_valid", 0),
                "usage_cost_eur": getattr(run, "usage_cost_eur", 0.0),
                "budget": getattr(run, "budget", {}),
                "snapshots_written": 0,
            }
            if run.status == "success" and run.results:
                _persist_snapshots(run.results)
                summary["snapshots_written"] = len(run.results)
                _log_event(
                    "sentiment_refresh_success",
                    f"{cfg.sentiment.provider} wrote {len(run.results)} snapshots",
                    payload=summary,
                )
            return summary

        # RSS lexicon path
        results = []
        try:
            results.append(provider.fetch_market_sentiment())
        except Exception as e:
            log.error("Market sentiment fetch failed: %s", e)
        try:
            results.extend(provider.fetch_sector_sentiment())
        except Exception as e:
            log.error("Sector sentiment fetch failed: %s", e)
        if hasattr(provider, "fetch_ticker_sentiment"):
            try:
                results.extend(provider.fetch_ticker_sentiment())
            except Exception as e:
                log.error("Ticker sentiment fetch failed: %s", e)

        _persist_snapshots(results)
        summary = {
            "status": "success" if results else "failed",
            "reason": "" if results else "no_results",
            "provider": cfg.sentiment.provider,
            "snapshots_written": len(results),
        }
        if results:
            _log_event(
                "sentiment_refresh_success",
                f"{cfg.sentiment.provider} wrote {len(results)} snapshots",
                payload=summary,
            )
        return summary
    finally:
        _REFRESH_LOCK.release()


def _persist_snapshots(results) -> None:
    now = utcnow()
    with get_db() as db:
        for r in results:
            db.add(SentimentSnapshot(
                timestamp=now,
                scope=r.scope,
                key=r.key,
                score=r.score,
                summary=r.summary or "",
                sources_json=json.dumps(r.sources) if isinstance(r.sources, list) else (r.sources or "[]"),
            ))


def _log_event(etype: str, msg: str, *, level: str = "INFO", payload: Optional[dict] = None) -> None:
    try:
        with get_db() as db:
            db.add(EventLog(
                timestamp=utcnow(),
                level=level,
                type=etype,
                message=msg[:2000],
                payload_json=json.dumps(payload or {}),
            ))
    except Exception:
        log.exception("Failed to write event log: %s", etype)
