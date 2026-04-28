"""Shared helper to refresh fundamentals for one or many symbols.

Used by the scheduler weekly tick, the `/api/v1/fundamentals/refresh` endpoint,
and the `python cli.py fundamentals refresh` command.
"""
from __future__ import annotations

import time
from typing import List, Optional

from common.logging import get_logger
from trader.fundamental_scorer import FundamentalScorer

log = get_logger(__name__)


def _resolve_symbols(symbols: Optional[List[str]], client) -> List[str]:
    if symbols:
        return [s.strip().upper() for s in symbols if s and s.strip()]
    from trader.universe import get_verified_universe

    return [item.symbol for item in get_verified_universe(client)]


def refresh_fundamentals(
    symbols: Optional[List[str]] = None,
    *,
    force: bool = True,
    client=None,
) -> dict:
    """Refresh yfinance fundamentals for the given symbols (or the full verified universe).

    Returns a dict: ``{refreshed, missing, errors, duration_s, symbols}``.
    One bad ticker never aborts the batch.
    """
    started = time.monotonic()
    target_symbols = _resolve_symbols(symbols, client)
    scorer = FundamentalScorer()

    refreshed = 0
    missing = 0
    errors: List[dict] = []

    for sym in target_symbols:
        try:
            result = scorer.get_score(sym, force_refresh=force)
            if result.get("source") in {"yfinance"} and result.get("total_score") is not None:
                refreshed += 1
            else:
                missing += 1
        except Exception as exc:
            errors.append({"symbol": sym, "error": str(exc)})
            log.warning("Fundamentals refresh failed for %s: %s", sym, exc)

    duration_s = round(time.monotonic() - started, 3)
    log.info(
        "Fundamentals refresh: refreshed=%d missing=%d errors=%d duration_s=%.2f",
        refreshed, missing, len(errors), duration_s,
    )
    return {
        "refreshed": refreshed,
        "missing": missing,
        "errors": errors,
        "duration_s": duration_s,
        "symbols": target_symbols,
    }
