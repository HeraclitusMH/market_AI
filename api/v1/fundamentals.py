"""POST /api/v1/fundamentals/refresh — force-refresh yfinance fundamentals."""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, HTTPException

from api.v1._refresh_log import log_refresh

router = APIRouter(tags=["v1"])


@router.post("/fundamentals/refresh")
def refresh_fundamentals_endpoint(symbol: Optional[str] = None) -> dict:
    """Recompute fundamentals.

    - No params  → refresh every symbol in the verified universe.
    - ?symbol=X  → refresh just that one symbol.
    """
    try:
        from trader.fundamentals_refresh import refresh_fundamentals
    except ModuleNotFoundError as exc:
        if exc.name == "yfinance":
            log_refresh("fundamentals", "error", 0, "yfinance not installed")
            raise HTTPException(
                status_code=503,
                detail=(
                    "Fundamentals refresh requires the yfinance package. "
                    "Rebuild the Docker image or install project dependencies."
                ),
            ) from exc
        raise

    symbols = [symbol] if symbol else None
    started = time.monotonic()
    try:
        result = refresh_fundamentals(symbols=symbols, force=True)
    except Exception as exc:
        log_refresh("fundamentals", "error", int((time.monotonic() - started) * 1000), str(exc))
        raise

    duration_ms = int((time.monotonic() - started) * 1000)
    refreshed = int(result.get("refreshed", 0) or 0)
    errors = result.get("errors") or []
    status = "error" if errors and refreshed == 0 else "success"
    scope = symbol or "universe"
    message = f"scope={scope} refreshed={refreshed} errors={len(errors)}"
    log_refresh("fundamentals", status, duration_ms, message)
    return result
