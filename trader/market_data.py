"""Market data fetching and caching."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd
from ib_insync import Stock

from common.config import get_config
from common.logging import get_logger
from common.time import utcnow, is_stale

log = get_logger(__name__)

# In-memory cache: (symbol, timeframe) -> (timestamp, DataFrame)
_cache: Dict[Tuple[str, str], Tuple[datetime, pd.DataFrame]] = {}

_TF_MAP = {
    "1D": ("1 Y", "1 day"),
    "1H": ("20 D", "1 hour"),
}


def _bars_to_df(bars: list) -> pd.DataFrame:
    if not bars:
        return pd.DataFrame()
    records = [
        {
            "date": b.date,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
        }
        for b in bars
    ]
    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def fetch_bars(symbol: str, timeframe: str = "1D", client=None) -> pd.DataFrame:
    """Fetch historical bars from IBKR, with cache."""
    cfg = get_config()
    cache_key = (symbol, timeframe)

    # check cache freshness
    if cache_key in _cache:
        ts, df = _cache[cache_key]
        if not is_stale(ts, cfg.safety.data_stale_minutes):
            return df

    if client is None:
        from trader.ibkr_client import get_ibkr_client
        client = get_ibkr_client()

    duration, bar_size = _TF_MAP.get(timeframe, ("60 D", "1 day"))
    contract = Stock(symbol, "SMART", "USD")

    try:
        bars = client.historical_bars(contract, duration=duration, bar_size=bar_size)
        df = _bars_to_df(bars)
        _cache[cache_key] = (utcnow(), df)
        log.debug("Fetched %d bars for %s/%s", len(df), symbol, timeframe)
        return df
    except Exception as e:
        log.error("Failed to fetch bars for %s: %s", symbol, e)
        # return stale data if available
        if cache_key in _cache:
            return _cache[cache_key][1]
        return pd.DataFrame()


def get_latest_bars(symbol: str, timeframe: str = "1D", client=None) -> pd.DataFrame:
    return fetch_bars(symbol, timeframe, client)


def is_data_fresh(symbol: str, timeframe: str = "1D") -> bool:
    cfg = get_config()
    cache_key = (symbol, timeframe)
    if cache_key not in _cache:
        return False
    ts, _ = _cache[cache_key]
    return not is_stale(ts, cfg.safety.data_stale_minutes)


def clear_cache() -> None:
    _cache.clear()
