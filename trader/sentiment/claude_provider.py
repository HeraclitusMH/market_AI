"""ClaudeLlmSentimentProvider — RSS headlines + Anthropic Claude → sentiment.

Pipeline per refresh:
    1. Fetch RSS headlines (title + snippet only, no scraping).
    2. Insert/update dedup table, drop items processed within the dedup window.
    3. Enforce the sentiment-only €/month + €/day budget cap.
    4. Shrink the batch if the remaining budget won't fit the default size.
    5. Call Claude with a strict JSON system prompt.
    6. Validate the batch; skip invalid items; fail the run if ALL invalid.
    7. Record usage (tokens + cost estimate) and mark items as processed.
    8. Aggregate into market / sector / ticker snapshots — written by the
       outer refresh_and_store() caller.

This provider returns a single :class:`ProviderRun` object so the caller can
persist snapshots atomically and log the right event type.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import feedparser
from pydantic import ValidationError

from common.config import SentimentClaudeConfig, SentimentRssConfig
from common.db import get_db
from common.logging import get_logger
from common.models import EventLog
from common.time import utcnow

from trader.sentiment.base import SentimentProvider, SentimentResult
from trader.sentiment import budget as budget_mod
from trader.sentiment import dedup as dedup_mod
from trader.sentiment.dedup import RawNewsItem
from trader.sentiment.llm_client import (
    AnthropicClient,
    LlmAuthError,
    LlmError,
    LlmResponse,
    LlmResponseFormatError,
    LlmTransientError,
)
from trader.sentiment.schemas import (
    LlmSentimentBatch,
    LlmSentimentItem,
    NewsItemForLlm,
)

log = get_logger(__name__)


SYSTEM_PROMPT = """\
You are a financial news sentiment extractor for a swing-trading system with a
1-2 week holding horizon.

You must output ONLY valid JSON matching the schema below. No markdown,
no code fences, no commentary, no text before or after the JSON.

Schema (TypeScript-ish):
{
  "model": string,
  "as_of": string,            // ISO 8601 UTC
  "items": [
    {
      "id": string,           // MUST echo one of the input ids exactly
      "entities": [
        { "type": "market" | "sector" | "ticker",
          "key": string,
          "sentiment": number,      // -1.0 to 1.0
          "confidence": number }    //  0.0 to 1.0
      ],
      "reasons": [string],    // 1-5 short bullets grounded in the text
      "key_phrases": [string] // optional; 0-10 short phrases
    }
  ]
}

Rules:
- Echo the input id verbatim for every item you return; do not invent ids.
- Every item MUST include exactly one market entity with key "US".
- Include a sector entity only if the headline clearly indicates one
  (e.g. "semiconductors", "banks", "energy"). Do not guess.
- Include a ticker entity only if the ticker is explicit or extremely obvious
  (e.g. "Apple" -> AAPL, "Nvidia" -> NVDA). Otherwise omit; never hallucinate.
- If the headline is ambiguous or not market-relevant, return a market entity
  with sentiment near 0 and a low confidence — do not skip the item.
- Multi-language: interpret the headline/snippet in its original language.
  Do not translate the output; reasons may be in English.
- Keep each reason bullet under 200 characters.
- sentiment and confidence must be numbers in the stated ranges.
"""


# ── Dataclass returned by a single refresh ──────────────────────────

@dataclass
class ProviderRun:
    status: str                                         # success | budget_stopped | failed | skipped
    reason: str = ""
    results: List[SentimentResult] = field(default_factory=list)
    items_sent: int = 0
    items_received: int = 0
    items_valid: int = 0
    usage_cost_eur: float = 0.0
    model: str = ""
    budget: Dict = field(default_factory=dict)


# ── Helpers ─────────────────────────────────────────────────────────

def _parse_published(entry) -> Optional[datetime]:
    pub = entry.get("published_parsed") or entry.get("updated_parsed")
    if not pub:
        return None
    try:
        return datetime(*pub[:6], tzinfo=timezone.utc)
    except Exception:
        return None


def _truncate(s: str, n: int) -> str:
    if not s:
        return ""
    s = s.strip()
    return s if len(s) <= n else s[: max(1, n - 1)].rstrip() + "\u2026"


def _extract_entries(cfg: SentimentRssConfig) -> List[Dict]:
    entries: List[Dict] = []
    for url in cfg.feeds:
        try:
            feed = feedparser.parse(
                url,
                request_headers={"User-Agent": cfg.user_agent},
            )
            for e in feed.entries:
                entries.append({
                    "title": e.get("title") or "",
                    "snippet": e.get("summary") or e.get("description") or "",
                    "url": e.get("link") or None,
                    "published_at": _parse_published(e),
                    "source": url,
                    "language": e.get("language"),
                })
                if len(entries) >= cfg.max_items_per_run:
                    return entries
        except Exception as exc:
            log.warning("Failed to fetch RSS %s: %s", url, exc)
    return entries


def _build_user_prompt(items: List[NewsItemForLlm]) -> str:
    payload = {
        "as_of": utcnow().isoformat(),
        "items": [
            {
                "id": it.id,
                "title": it.title,
                "snippet": it.snippet,
                "url": it.url,
                "published_at": it.published_at.isoformat() if it.published_at else None,
                "source": it.source,
                "language": it.language,
            }
            for it in items
        ],
    }
    return (
        "Score the sentiment of each news item below. "
        "Output ONLY the JSON object per the schema.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )


# ── Provider ────────────────────────────────────────────────────────

class ClaudeLlmSentimentProvider(SentimentProvider):
    """LLM-based sentiment provider. See module docstring for pipeline details."""

    def __init__(
        self,
        claude_cfg: SentimentClaudeConfig,
        rss_cfg: SentimentRssConfig,
        client: Optional[AnthropicClient] = None,
    ):
        self.claude_cfg = claude_cfg
        self.rss_cfg = rss_cfg
        self.client = client or AnthropicClient(
            api_key_env=claude_cfg.api_key_env,
            model=claude_cfg.model,
            timeout_seconds=claude_cfg.request_timeout_seconds,
            max_retries=claude_cfg.max_retries,
            backoff_base_seconds=claude_cfg.backoff_base_seconds,
            backoff_max_seconds=claude_cfg.backoff_max_seconds,
        )

    # The ABC splits market vs sector; the LLM produces both in one pass,
    # so we expose a unified run() and stub the ABC methods for compatibility.
    def fetch_market_sentiment(self) -> SentimentResult:
        run = self.run()
        for r in run.results:
            if r.scope == "market" and r.key.upper() == "US":
                return r
        return SentimentResult(scope="market", key="US", score=0.0, summary=run.reason or "No data", sources=[])

    def fetch_sector_sentiment(self) -> List[SentimentResult]:
        run = self.run()
        return [r for r in run.results if r.scope == "sector"]

    # ---------- main entry ----------

    def run(self, now: Optional[datetime] = None) -> ProviderRun:
        """Execute one end-to-end refresh and return a ProviderRun."""
        from trader.sentiment import aggregate as aggregate_mod  # local import avoids cycles

        now = now or utcnow()
        run = ProviderRun(status="failed", model=self.claude_cfg.model)

        # 1. Fetch RSS
        try:
            entries = _extract_entries(self.rss_cfg)
        except Exception as e:
            self._log_event("sentiment_refresh_failed", f"RSS fetch failed: {e}", level="ERROR")
            run.reason = f"rss_fetch_failed: {e}"
            return run
        if not entries:
            run.status = "skipped"
            run.reason = "no_rss_entries"
            return run

        # 2. Dedup + 3/4. Budget pre-flight — all in one DB transaction so we
        # don't leave dedup bookkeeping behind if the budget stops the run.
        with get_db() as db:
            status = budget_mod.get_status(
                db,
                monthly_budget_eur=self.claude_cfg.monthly_budget_eur,
                daily_budget_fraction=self.claude_cfg.daily_budget_fraction,
                eur_usd_rate=self.claude_cfg.eur_usd_rate,
                hard_stop_on_budget=self.claude_cfg.hard_stop_on_budget,
                now=now,
            )
            run.budget = status.as_dict()

            if status.budget_stopped:
                self._log_event(
                    "sentiment_refresh_budget_stopped",
                    f"LLM budget cap hit: {status.reason}. Skipping Claude call.",
                    level="WARNING",
                    payload=status.as_dict(),
                )
                run.status = "budget_stopped"
                run.reason = status.reason or "budget_stopped"
                return run

            raw_items = [
                RawNewsItem(
                    title=_truncate(e["title"], self.claude_cfg.max_chars_per_item),
                    snippet=_truncate(e["snippet"], self.claude_cfg.max_chars_per_item),
                    url=e["url"],
                    published_at=e["published_at"],
                    source=e["source"],
                    language=e.get("language"),
                )
                for e in entries
            ]
            new_pairs = dedup_mod.upsert_and_filter_new(
                db, raw_items, dedup_window_days=self.claude_cfg.dedup_cache_days, now=now,
            )
            if not new_pairs:
                run.status = "skipped"
                run.reason = "all_items_deduped"
                return run

            # 4. Shrink the batch to fit the per-run cap and the remaining budget.
            new_pairs = new_pairs[: self.claude_cfg.max_items_per_run]
            remaining_eur = min(status.remaining_month_eur, status.remaining_today_eur)
            # Use worst-case completion tokens (cap / batch_size) for the estimate.
            fittable = budget_mod.max_items_that_fit(
                remaining_eur=remaining_eur,
                eur_usd_rate=self.claude_cfg.eur_usd_rate,
                model=self.claude_cfg.model,
                per_item_prompt_token_estimate=self.claude_cfg.max_tokens_per_item_estimate,
                per_item_completion_token_estimate=self.claude_cfg.max_tokens_per_item_estimate,
            )
            batch_size = min(len(new_pairs), fittable)
            if batch_size <= 0:
                self._log_event(
                    "sentiment_refresh_budget_stopped",
                    "Remaining budget cannot fit a single item; skipping call.",
                    level="WARNING",
                    payload=status.as_dict(),
                )
                run.status = "budget_stopped"
                run.reason = "budget_insufficient_for_any_item"
                return run
            new_pairs = new_pairs[:batch_size]

            # 5. Build prompt + call Claude
            items_for_llm = [
                NewsItemForLlm(
                    id=hid,
                    title=item.title,
                    snippet=item.snippet,
                    url=item.url,
                    published_at=item.published_at,
                    source=item.source,
                    language=item.language,
                )
                for hid, item in new_pairs
            ]
            user_prompt = _build_user_prompt(items_for_llm)
            run.items_sent = len(items_for_llm)

            try:
                resp: LlmResponse = self.client.complete_json(
                    system=SYSTEM_PROMPT,
                    user=user_prompt,
                    max_tokens=self.claude_cfg.max_output_tokens,
                    temperature=self.claude_cfg.temperature,
                )
            except LlmAuthError as e:
                self._log_event("sentiment_refresh_failed", f"Anthropic auth error: {e}", level="ERROR")
                run.reason = f"auth_error: {e}"
                budget_mod.record_usage(
                    db, model=self.claude_cfg.model, input_items_count=len(items_for_llm),
                    prompt_tokens=None, completion_tokens=None, cost_usd=0.0,
                    eur_usd_rate=self.claude_cfg.eur_usd_rate,
                    anthropic_request_id=None, status="failed",
                    error_type="auth_error", error_message=str(e),
                )
                return run
            except (LlmTransientError, LlmResponseFormatError, LlmError) as e:
                self._log_event("sentiment_refresh_failed", f"Anthropic call failed: {e}", level="ERROR")
                run.reason = f"llm_error: {e}"
                budget_mod.record_usage(
                    db, model=self.claude_cfg.model, input_items_count=len(items_for_llm),
                    prompt_tokens=None, completion_tokens=None, cost_usd=0.0,
                    eur_usd_rate=self.claude_cfg.eur_usd_rate,
                    anthropic_request_id=None, status="failed",
                    error_type=type(e).__name__, error_message=str(e),
                )
                return run

            # 6. Validate response
            try:
                batch = LlmSentimentBatch.model_validate(resp.data)
            except ValidationError as e:
                self._log_event("sentiment_refresh_failed", f"LLM output failed schema: {e}", level="ERROR")
                budget_mod.record_usage(
                    db, model=resp.model, input_items_count=len(items_for_llm),
                    prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens,
                    cost_usd=_cost_from_response(resp, self.claude_cfg, len(user_prompt), len(items_for_llm)),
                    eur_usd_rate=self.claude_cfg.eur_usd_rate,
                    anthropic_request_id=resp.request_id, status="failed",
                    error_type="schema_error", error_message=str(e)[:2000],
                )
                run.reason = "schema_error"
                return run

            # Item-level validation — drop invalid items, don't fail the whole batch.
            requested_ids = {it.id for it in items_for_llm}
            valid_items: List[LlmSentimentItem] = []
            for it in batch.items:
                if it.id not in requested_ids:
                    continue  # fabricated id — ignore
                valid_items.append(it)
            run.items_received = len(batch.items)
            run.items_valid = len(valid_items)

            # 7. Record usage + mark processed
            cost_usd = _cost_from_response(resp, self.claude_cfg, len(user_prompt), len(items_for_llm))
            usage_row = budget_mod.record_usage(
                db, model=resp.model, input_items_count=len(items_for_llm),
                prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens,
                cost_usd=cost_usd, eur_usd_rate=self.claude_cfg.eur_usd_rate,
                anthropic_request_id=resp.request_id, status="success",
            )
            run.usage_cost_eur = usage_row.cost_eur_est

            if not valid_items:
                self._log_event(
                    "sentiment_refresh_failed",
                    "LLM returned no valid items — keeping prior snapshots.",
                    level="ERROR",
                    payload={"received": run.items_received, "sent": run.items_sent},
                )
                run.reason = "all_items_invalid"
                return run

            dedup_mod.mark_processed(db, [it.id for it in valid_items], now=now)

            # 8. Aggregate — produce SentimentResult rows for persistence.
            lookup = {it.id: it for it in items_for_llm}
            run.results = aggregate_mod.aggregate(
                items_for_llm=[lookup[it.id] for it in valid_items if it.id in lookup],
                llm_items=valid_items,
                min_confidence=self.claude_cfg.min_confidence_to_use,
                now=now,
            )

            run.status = "success"
            return run

    # ---------- internals ----------

    def _log_event(self, etype: str, msg: str, *, level: str = "INFO", payload: Optional[Dict] = None) -> None:
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
            # Never let event logging mask a real error.
            log.exception("Failed to write event log: %s", etype)


def _cost_from_response(
    resp: LlmResponse,
    cfg: SentimentClaudeConfig,
    user_prompt_len: int,
    item_count: int,
) -> float:
    """Use reported token counts when present; fall back to a conservative estimate."""
    if resp.prompt_tokens is not None and resp.completion_tokens is not None:
        return budget_mod.estimate_cost_usd(
            resp.model,
            prompt_tokens=int(resp.prompt_tokens),
            completion_tokens=int(resp.completion_tokens),
        )
    est_prompt = budget_mod.estimate_prompt_tokens_from_text(
        "x" * user_prompt_len,  # length-based estimate
    )
    # Conservative: assume worst-case output per item.
    est_completion = min(cfg.max_output_tokens, cfg.max_tokens_per_item_estimate * max(1, item_count))
    return budget_mod.estimate_cost_usd(
        resp.model,
        prompt_tokens=est_prompt,
        completion_tokens=est_completion,
    )
