"""Universe management — seed tickers, filter by liquidity."""
from __future__ import annotations

import json
from typing import List

from common.config import get_config
from common.db import get_db
from common.logging import get_logger
from common.models import Universe

log = get_logger(__name__)

# Embedded seed universe — liquid US stocks + major ETFs
SEED_TICKERS = [
    # Major ETFs
    ("SPY", "ETF", "Broad Market"), ("QQQ", "ETF", "Technology"),
    ("IWM", "ETF", "Small Cap"), ("DIA", "ETF", "Broad Market"),
    # Sector ETFs
    ("XLF", "ETF", "Financial"), ("XLK", "ETF", "Technology"),
    ("XLE", "ETF", "Energy"), ("XLV", "ETF", "Healthcare"),
    ("XLI", "ETF", "Industrial"), ("XLY", "ETF", "Consumer Discretionary"),
    ("XLP", "ETF", "Consumer Staples"), ("XLU", "ETF", "Utilities"),
    ("XLB", "ETF", "Materials"), ("XLRE", "ETF", "Real Estate"),
    ("XLC", "ETF", "Communication"),
    # Large-cap stocks
    ("AAPL", "STK", "Technology"), ("MSFT", "STK", "Technology"),
    ("AMZN", "STK", "Consumer Discretionary"), ("GOOGL", "STK", "Technology"),
    ("META", "STK", "Technology"), ("NVDA", "STK", "Technology"),
    ("TSLA", "STK", "Consumer Discretionary"), ("JPM", "STK", "Financial"),
    ("V", "STK", "Financial"), ("JNJ", "STK", "Healthcare"),
    ("UNH", "STK", "Healthcare"), ("HD", "STK", "Consumer Discretionary"),
    ("PG", "STK", "Consumer Staples"), ("MA", "STK", "Financial"),
    ("DIS", "STK", "Communication"), ("BAC", "STK", "Financial"),
    ("XOM", "STK", "Energy"), ("CVX", "STK", "Energy"),
    ("ABBV", "STK", "Healthcare"), ("KO", "STK", "Consumer Staples"),
    ("PEP", "STK", "Consumer Staples"), ("MRK", "STK", "Healthcare"),
    ("COST", "STK", "Consumer Staples"), ("AVGO", "STK", "Technology"),
    ("TMO", "STK", "Healthcare"), ("ACN", "STK", "Technology"),
    ("MCD", "STK", "Consumer Discretionary"), ("LIN", "STK", "Materials"),
    ("AMD", "STK", "Technology"), ("INTC", "STK", "Technology"),
    ("CRM", "STK", "Technology"), ("NFLX", "STK", "Communication"),
    ("ADBE", "STK", "Technology"), ("WMT", "STK", "Consumer Staples"),
    ("T", "STK", "Communication"), ("VZ", "STK", "Communication"),
    ("PFE", "STK", "Healthcare"), ("NKE", "STK", "Consumer Discretionary"),
    ("CSCO", "STK", "Technology"), ("ORCL", "STK", "Technology"),
]


def seed_universe() -> int:
    """Insert seed tickers into DB if not present. Returns count added."""
    added = 0
    with get_db() as db:
        for symbol, stype, sector in SEED_TICKERS:
            existing = db.query(Universe).filter(Universe.symbol == symbol).first()
            if existing is None:
                db.add(Universe(symbol=symbol, type=stype, sector=sector, active=True))
                added += 1
    log.info("Seeded %d tickers into universe.", added)
    return added


def refresh_universe(client=None) -> List[str]:
    """Filter universe by liquidity using IBKR data. Returns list of active symbols."""
    from trader.market_data import fetch_bars
    cfg = get_config()
    active_symbols = []

    with get_db() as db:
        all_tickers = db.query(Universe).all()

    for ticker in all_tickers:
        try:
            df = fetch_bars(ticker.symbol, "1D", client)
            if df.empty or len(df) < 5:
                _set_active(ticker.symbol, False, "{}")
                continue

            # compute avg dollar volume over last 20 bars (or available)
            recent = df.tail(20)
            avg_dollar_vol = (recent["close"] * recent["volume"]).mean()
            last_price = df["close"].iloc[-1]

            metrics = {
                "avg_dollar_volume": round(avg_dollar_vol, 2),
                "last_price": round(last_price, 2),
                "bars_available": len(df),
            }

            is_active = (
                avg_dollar_vol >= cfg.universe.min_dollar_volume
                and last_price >= cfg.universe.min_price
            )

            _set_active(ticker.symbol, is_active, json.dumps(metrics))
            if is_active:
                active_symbols.append(ticker.symbol)

        except Exception as e:
            log.warning("Failed to refresh %s: %s", ticker.symbol, e)
            _set_active(ticker.symbol, False, "{}")

    log.info("Universe refresh: %d active out of %d total", len(active_symbols), len(all_tickers))
    return active_symbols


def _set_active(symbol: str, active: bool, metrics_json: str) -> None:
    with get_db() as db:
        row = db.query(Universe).filter(Universe.symbol == symbol).first()
        if row:
            row.active = active
            row.liquidity_metrics_json = metrics_json


def get_active_symbols() -> List[str]:
    with get_db() as db:
        rows = db.query(Universe).filter(Universe.active == True).all()
    return [r.symbol for r in rows]
