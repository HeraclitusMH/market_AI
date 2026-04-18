"""Hard-cap budget limiter for the Claude sentiment provider.

All pricing and spend accounting is sentiment-only — it does NOT include any
other Anthropic usage elsewhere in the project.

Update `MODEL_PRICING_USD_PER_MTOK` from the Anthropic pricing page whenever
pricing changes. For a real hard stop, ALSO configure a spend limit in the
Anthropic Console — this in-process cap is a best-effort safety net, not a
substitute for provider-side enforcement.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, date, time, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from common.logging import get_logger
from common.models import SentimentLlmUsage
from common.time import utcnow

log = get_logger(__name__)


# ── Pricing table (USD per 1M tokens) ──────────────────────────────
# Keep this up to date with https://www.anthropic.com/pricing.
# Values below are conservative defaults; override via update if pricing changes.
MODEL_PRICING_USD_PER_MTOK: Dict[str, Dict[str, float]] = {
    # Claude 3.5 Sonnet
    "claude-3-5-sonnet-latest": {"input": 3.00, "output": 15.00},
    "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00},
    # Claude 3 Opus
    "claude-3-opus-latest": {"input": 15.00, "output": 75.00},
    # Claude 3 Haiku
    "claude-3-5-haiku-latest": {"input": 1.00, "output": 5.00},
    # Newer families — keep conservative defaults until confirmed.
    "claude-4-7-sonnet-latest": {"input": 3.00, "output": 15.00},
    "claude-4-7-opus-latest": {"input": 15.00, "output": 75.00},
}

_DEFAULT_PRICING = {"input": 3.00, "output": 15.00}


def pricing_for(model: str) -> Dict[str, float]:
    return MODEL_PRICING_USD_PER_MTOK.get(model, _DEFAULT_PRICING)


# ── Cost estimation ─────────────────────────────────────────────────

def estimate_prompt_tokens_from_text(text: str) -> int:
    """Rough token estimate: ~4 chars per token. Conservative for English.

    For non-English text this over-counts, which is fine for a *budget cap* —
    we prefer to overshoot the estimate than undershoot it.
    """
    return max(1, len(text) // 4)


def estimate_cost_usd(
    model: str,
    *,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    price = pricing_for(model)
    return (
        (prompt_tokens / 1_000_000.0) * price["input"]
        + (completion_tokens / 1_000_000.0) * price["output"]
    )


# ── Spend tracking ──────────────────────────────────────────────────

@dataclass
class BudgetStatus:
    month_to_date_usd: float
    today_usd: float
    month_to_date_eur: float
    today_eur: float
    monthly_cap_eur: float
    daily_cap_eur: float
    remaining_month_eur: float
    remaining_today_eur: float
    budget_stopped: bool
    reason: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "month_to_date_usd": round(self.month_to_date_usd, 4),
            "today_usd": round(self.today_usd, 4),
            "month_to_date_eur": round(self.month_to_date_eur, 4),
            "today_eur": round(self.today_eur, 4),
            "monthly_cap_eur": round(self.monthly_cap_eur, 2),
            "daily_cap_eur": round(self.daily_cap_eur, 2),
            "remaining_month_eur": round(self.remaining_month_eur, 4),
            "remaining_today_eur": round(self.remaining_today_eur, 4),
            "budget_stopped": self.budget_stopped,
            "reason": self.reason,
        }


def _month_start_utc(now: datetime) -> datetime:
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc)


def _day_start_utc(now: datetime) -> datetime:
    return datetime(now.year, now.month, now.day, tzinfo=timezone.utc)


def _sum_cost(db: Session, *, since: datetime) -> Tuple[float, float]:
    """Return (usd, eur) spent since the given timestamp (inclusive)."""
    usd, eur = db.query(
        func.coalesce(func.sum(SentimentLlmUsage.cost_usd_est), 0.0),
        func.coalesce(func.sum(SentimentLlmUsage.cost_eur_est), 0.0),
    ).filter(SentimentLlmUsage.ts >= since).one()
    return float(usd or 0.0), float(eur or 0.0)


def get_status(
    db: Session,
    *,
    monthly_budget_eur: float,
    daily_budget_fraction: float,
    eur_usd_rate: float,
    hard_stop_on_budget: bool,
    now: Optional[datetime] = None,
) -> BudgetStatus:
    now = now or utcnow()
    month_usd, month_eur = _sum_cost(db, since=_month_start_utc(now))
    today_usd, today_eur = _sum_cost(db, since=_day_start_utc(now))

    monthly_cap = float(monthly_budget_eur)
    daily_cap = monthly_cap * float(daily_budget_fraction)
    remaining_month = max(0.0, monthly_cap - month_eur)
    remaining_today = max(0.0, daily_cap - today_eur)

    stopped = False
    reason: Optional[str] = None
    if hard_stop_on_budget:
        if month_eur >= monthly_cap:
            stopped, reason = True, "monthly_budget_exhausted"
        elif today_eur >= daily_cap:
            stopped, reason = True, "daily_budget_exhausted"

    return BudgetStatus(
        month_to_date_usd=month_usd,
        today_usd=today_usd,
        month_to_date_eur=month_eur,
        today_eur=today_eur,
        monthly_cap_eur=monthly_cap,
        daily_cap_eur=daily_cap,
        remaining_month_eur=remaining_month,
        remaining_today_eur=remaining_today,
        budget_stopped=stopped,
        reason=reason,
    )


# ── Pre-flight batch sizing ─────────────────────────────────────────

def max_items_that_fit(
    *,
    remaining_eur: float,
    eur_usd_rate: float,
    model: str,
    per_item_prompt_token_estimate: int,
    per_item_completion_token_estimate: int,
) -> int:
    """Compute the largest item count whose estimated cost fits in `remaining_eur`.

    Returns 0 when not even a single item fits.
    """
    if remaining_eur <= 0:
        return 0
    per_item_usd = estimate_cost_usd(
        model,
        prompt_tokens=per_item_prompt_token_estimate,
        completion_tokens=per_item_completion_token_estimate,
    )
    if per_item_usd <= 0:
        # shouldn't happen, but don't divide by zero
        return 10**6
    per_item_eur = per_item_usd / max(eur_usd_rate, 1e-9)
    if per_item_eur <= 0:
        return 10**6
    return max(0, int(remaining_eur // per_item_eur))


# ── Usage record writer ─────────────────────────────────────────────

def record_usage(
    db: Session,
    *,
    model: str,
    input_items_count: int,
    prompt_tokens: Optional[int],
    completion_tokens: Optional[int],
    cost_usd: float,
    eur_usd_rate: float,
    anthropic_request_id: Optional[str],
    status: str = "success",
    error_type: Optional[str] = None,
    error_message: Optional[str] = None,
) -> SentimentLlmUsage:
    row = SentimentLlmUsage(
        id=uuid.uuid4().hex,
        ts=utcnow(),
        provider="anthropic",
        model=model,
        request_kind="sentiment_extraction",
        input_items_count=int(input_items_count),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd_est=round(float(cost_usd), 6),
        cost_eur_est=round(float(cost_usd) / max(float(eur_usd_rate), 1e-9), 6),
        anthropic_request_id=anthropic_request_id,
        status=status,
        error_type=error_type,
        error_message=(error_message or "")[:2000] or None,
    )
    db.add(row)
    db.flush()
    return row
