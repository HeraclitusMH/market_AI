"""POST /api/v1/fundamentals/refresh — force-refresh yfinance fundamentals."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

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
            raise HTTPException(
                status_code=503,
                detail=(
                    "Fundamentals refresh requires the yfinance package. "
                    "Rebuild the Docker image or install project dependencies."
                ),
            ) from exc
        raise

    symbols = [symbol] if symbol else None
    result = refresh_fundamentals(symbols=symbols, force=True)
    return result
