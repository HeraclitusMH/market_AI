"""Deterministic company-name → ticker matcher.

Strategy (v1 — exact alias only):
    1. normalize_company_name(input)
    2. Look up alias in security_alias JOIN security_master (active + allowed exchange)
    3. If exactly one result → match
    4. If 0 or 2+ → no match (skip or ambiguous)

No fuzzy matching in v1.  The audit table (rss_entity_matches) captures every
attempt so we can improve aliases from real-world misses.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from common.config import get_config
from common.db import get_db
from common.logging import get_logger
from common.models import RssEntityMatch, SecurityAlias, SecurityMaster
from common.time import utcnow
from trader.securities.normalize import normalize_company_name

log = get_logger(__name__)


@dataclass
class MatchResult:
    company_input: str
    normalized: str
    symbol: Optional[str]
    match_type: str          # exact_alias | ambiguous | unmatched
    match_score: float       # 1.0 for exact, 0.0 for no match
    reason: Optional[str] = None
    verified_ibkr: bool = False


def match_companies_to_symbols(
    companies: list[str],
    article_id: str,
    *,
    write_audit: bool = True,
) -> list[MatchResult]:
    """Map a list of company-name strings to verified symbols.

    Always returns a MatchResult for every input.  Call sites should filter
    on result.symbol is not None before using the match.
    """
    if not companies:
        return []

    cfg = get_config().securities
    allowed = [e.upper() for e in cfg.allowed_exchanges]
    results: list[MatchResult] = []
    now = utcnow().replace(tzinfo=None)

    with get_db() as db:
        for company in companies:
            normalized = normalize_company_name(company)
            result = _lookup(db, normalized, company, allowed)
            results.append(result)

            if write_audit:
                db.add(RssEntityMatch(
                    article_id=article_id,
                    company_input=company[:500],
                    normalized_input=normalized[:500],
                    symbol=result.symbol,
                    match_type=result.match_type,
                    match_score=result.match_score if result.symbol else None,
                    reason=result.reason,
                    created_at=now,
                ))

    return results


def _lookup(db, normalized: str, original: str, allowed_exchanges: list[str]) -> MatchResult:
    """Single DB lookup for one normalized alias."""
    if not normalized:
        return MatchResult(
            company_input=original,
            normalized=normalized,
            symbol=None,
            match_type="unmatched",
            match_score=0.0,
            reason="empty_after_normalization",
        )

    # Fetch up to 2 results to detect ambiguity.
    # Join ensures we only match active securities on allowed exchanges.
    rows = (
        db.query(SecurityAlias.symbol, SecurityAlias.priority)
        .join(SecurityMaster, SecurityAlias.symbol == SecurityMaster.symbol)
        .filter(
            SecurityAlias.alias == normalized,
            SecurityMaster.active == True,
            SecurityMaster.exchange.in_(allowed_exchanges),
        )
        .order_by(SecurityAlias.priority.asc())
        .limit(2)
        .all()
    )

    if not rows:
        log.debug("No alias match for '%s' (normalized: '%s')", original, normalized)
        return MatchResult(
            company_input=original,
            normalized=normalized,
            symbol=None,
            match_type="unmatched",
            match_score=0.0,
            reason="no_alias_found",
        )

    if len(rows) > 1 and rows[0].symbol != rows[1].symbol:
        log.debug(
            "Ambiguous match for '%s': candidates %s vs %s",
            original, rows[0].symbol, rows[1].symbol,
        )
        return MatchResult(
            company_input=original,
            normalized=normalized,
            symbol=None,
            match_type="ambiguous",
            match_score=0.0,
            reason=f"ambiguous:{rows[0].symbol}|{rows[1].symbol}",
        )

    symbol = rows[0].symbol
    log.debug("Matched '%s' → %s (priority=%d)", original, symbol, rows[0].priority)
    return MatchResult(
        company_input=original,
        normalized=normalized,
        symbol=symbol,
        match_type="exact_alias",
        match_score=1.0,
        reason="ok",
        verified_ibkr=False,  # IBKR check is separate; rely on import-time verification
    )
