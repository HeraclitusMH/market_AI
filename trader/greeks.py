"""Greeks data layer: live Greeks fetching, caching, and IV Rank computation.

Primary source is IBKR's modelGreeks (tick #13) via ib_insync Ticker.
Falls back to lastGreeks / bidGreeks-askGreeks midpoints when unavailable.
"""
from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from ib_insync import Option, Stock

from common.logging import get_logger
from trader.ibkr_client import IBKRClient

log = get_logger(__name__)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _sanitize_price(v) -> Optional[float]:
    """IBKR uses -1.0 to mean 'not available'. NaN / None also invalid."""
    if v is None:
        return None
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(fv) or fv <= 0.0:
        return None
    return fv


@dataclass
class GreeksSnapshot:
    """Snapshot of Greeks for a single option contract."""
    symbol: str
    expiration: str              # YYYYMMDD
    strike: float
    right: str                   # 'C' or 'P'

    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    implied_vol: Optional[float] = None

    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    mid: Optional[float] = None
    model_price: Optional[float] = None

    open_interest: Optional[int] = None
    volume: Optional[int] = None

    underlying_price: Optional[float] = None

    timestamp: datetime = field(default_factory=datetime.utcnow)
    data_quality: str = "unknown"  # "live", "delayed", "stale", "partial"

    @property
    def is_valid(self) -> bool:
        """Minimum viable: delta and implied_vol present."""
        return self.delta is not None and self.implied_vol is not None

    @property
    def abs_delta(self) -> Optional[float]:
        return abs(self.delta) if self.delta is not None else None

    @property
    def moneyness(self) -> Optional[str]:
        ad = self.abs_delta
        if ad is None:
            return None
        if ad > 0.55:
            return "ITM"
        if ad > 0.45:
            return "ATM"
        return "OTM"

    @property
    def bid_ask_spread_pct(self) -> Optional[float]:
        if self.bid is None or self.ask is None:
            return None
        mid = (self.bid + self.ask) / 2.0
        if mid <= 0:
            return None
        return (self.ask - self.bid) / mid


@dataclass
class OptionChainGreeks:
    """Full option chain with Greeks data for a symbol/expiration."""
    symbol: str
    expiration: str
    underlying_price: float
    calls: List[GreeksSnapshot] = field(default_factory=list)
    puts: List[GreeksSnapshot] = field(default_factory=list)
    iv_rank: Optional[float] = None
    iv_percentile: Optional[float] = None
    historical_vol: Optional[float] = None
    fetch_timestamp: datetime = field(default_factory=datetime.utcnow)

    def leg(self, right: str) -> List[GreeksSnapshot]:
        return self.calls if right.upper() == "C" else self.puts

    def valid_legs(self, right: str) -> List[GreeksSnapshot]:
        return [s for s in self.leg(right) if s.is_valid]


class GreeksService:
    """Service to fetch and cache live Greeks data from IBKR."""

    def __init__(self, ibkr_client: IBKRClient) -> None:
        self.client = ibkr_client
        self._cache: Dict[str, Tuple[float, OptionChainGreeks]] = {}
        self._iv_cache: Dict[str, Tuple[float, Optional[float]]] = {}
        self._cache_ttl = _env_int("GREEKS_CACHE_TTL_SECONDS", 30)
        self._strike_range_pct = _env_float("GREEKS_STRIKE_RANGE_PCT", 0.10)
        self._data_wait = _env_float("GREEKS_DATA_WAIT_SECONDS", 2.0)
        self._iv_lookback_days = _env_int("GREEKS_IV_LOOKBACK_DAYS", 252)

    # ── chain fetching ────────────────────────────────────

    def fetch_chain_greeks(
        self,
        symbol: str,
        expiration: str,
        strikes: Optional[List[float]] = None,
        rights: Optional[List[str]] = None,
        strike_range_pct: Optional[float] = None,
        exchange: str = "SMART",
    ) -> OptionChainGreeks:
        """Fetch Greeks for an option chain.

        If strikes is None, auto-selects strikes within strike_range_pct of the
        underlying price from the chain's published strikes.
        """
        if rights is None:
            rights = ["C", "P"]
        if strike_range_pct is None:
            strike_range_pct = self._strike_range_pct

        cache_key = f"{symbol}|{expiration}|{strike_range_pct}"
        cached = self._cache.get(cache_key)
        if cached and (time.time() - cached[0]) < self._cache_ttl:
            return cached[1]

        underlying_price = self._fetch_underlying_price(symbol, exchange)

        if strikes is None:
            strikes = self._select_strikes_in_range(
                symbol, expiration, underlying_price, strike_range_pct, exchange
            )

        if not strikes:
            log.warning("No strikes to fetch for %s exp=%s", symbol, expiration)
            return OptionChainGreeks(
                symbol=symbol,
                expiration=expiration,
                underlying_price=underlying_price,
            )

        contracts = [
            Option(symbol, expiration, strike, right, exchange)
            for strike in strikes
            for right in rights
        ]

        try:
            qualified = self.client.ib.qualifyContracts(*contracts)
        except Exception as e:
            log.error("qualifyContracts failed for %s: %s", symbol, e)
            return OptionChainGreeks(
                symbol=symbol,
                expiration=expiration,
                underlying_price=underlying_price,
            )

        qualified = [c for c in qualified if getattr(c, "conId", 0)]
        if not qualified:
            log.warning("No contracts qualified for %s exp=%s", symbol, expiration)
            return OptionChainGreeks(
                symbol=symbol,
                expiration=expiration,
                underlying_price=underlying_price,
            )

        # generic ticks: 100=option vol, 101=OI, 104=HV, 106=IV
        generic_ticks = "100,101,104,106"
        tickers = []
        for c in qualified:
            try:
                t = self.client.ib.reqMktData(c, generic_ticks, snapshot=False, regulatorySnapshot=False)
                tickers.append(t)
            except Exception as e:
                log.warning("reqMktData failed for %s %s: %s", symbol, c.strike, e)

        self.client.ib.sleep(self._data_wait)

        calls: List[GreeksSnapshot] = []
        puts: List[GreeksSnapshot] = []
        for ticker in tickers:
            snap = self._parse_ticker_to_snapshot(ticker, underlying_price)
            if snap is None:
                continue
            if snap.right == "C":
                calls.append(snap)
            else:
                puts.append(snap)

        # best-effort cancel of streaming subscriptions
        for t in tickers:
            try:
                self.client.ib.cancelMktData(t.contract)
            except Exception:
                pass

        calls.sort(key=lambda s: s.strike)
        puts.sort(key=lambda s: s.strike)

        chain = OptionChainGreeks(
            symbol=symbol,
            expiration=expiration,
            underlying_price=underlying_price,
            calls=calls,
            puts=puts,
        )
        chain.iv_rank = self.get_iv_rank(symbol)
        chain.historical_vol = self._get_historical_vol(symbol)

        self._cache[cache_key] = (time.time(), chain)
        return chain

    # ── ticker → snapshot parsing ─────────────────────────

    def _parse_ticker_to_snapshot(
        self, ticker, underlying_price: Optional[float]
    ) -> Optional[GreeksSnapshot]:
        c = getattr(ticker, "contract", None)
        if c is None:
            return None
        right = getattr(c, "right", "") or ""
        if right not in ("C", "P"):
            return None

        bid = _sanitize_price(getattr(ticker, "bid", None))
        ask = _sanitize_price(getattr(ticker, "ask", None))
        last = _sanitize_price(getattr(ticker, "last", None))
        mid = (bid + ask) / 2.0 if (bid is not None and ask is not None) else None

        delta = gamma = theta = vega = iv = model_price = None
        quality = "unknown"
        for attr, label in (
            ("modelGreeks", "live"),
            ("lastGreeks", "live"),
        ):
            g = getattr(ticker, attr, None)
            if g is None:
                continue
            if getattr(g, "delta", None) is not None:
                delta = g.delta
                gamma = getattr(g, "gamma", None)
                theta = getattr(g, "theta", None)
                vega = getattr(g, "vega", None)
                iv = getattr(g, "impliedVol", None)
                model_price = _sanitize_price(getattr(g, "optPrice", None))
                quality = label
                break

        # last resort: average of bid/ask greeks
        if delta is None:
            bg = getattr(ticker, "bidGreeks", None)
            ag = getattr(ticker, "askGreeks", None)
            if bg is not None and ag is not None and bg.delta is not None and ag.delta is not None:
                delta = (bg.delta + ag.delta) / 2.0
                gamma = _avg(bg.gamma, ag.gamma)
                theta = _avg(bg.theta, ag.theta)
                vega = _avg(bg.vega, ag.vega)
                iv = _avg(bg.impliedVol, ag.impliedVol)
                quality = "partial"

        if delta is None and iv is None:
            quality = "stale"

        oi = None
        if right == "C":
            oi = getattr(ticker, "callOpenInterest", None)
        elif right == "P":
            oi = getattr(ticker, "putOpenInterest", None)

        volume = getattr(ticker, "volume", None)
        if volume is not None:
            try:
                volume = int(volume) if not math.isnan(volume) else None
            except (TypeError, ValueError):
                volume = None

        if oi is not None:
            try:
                oi = int(oi) if not math.isnan(oi) else None
            except (TypeError, ValueError):
                oi = None

        return GreeksSnapshot(
            symbol=c.symbol,
            expiration=c.lastTradeDateOrContractMonth,
            strike=float(c.strike),
            right=right,
            delta=delta,
            gamma=gamma,
            theta=theta,
            vega=vega,
            implied_vol=iv,
            bid=bid,
            ask=ask,
            last=last,
            mid=mid,
            model_price=model_price,
            open_interest=oi,
            volume=volume,
            underlying_price=underlying_price,
            data_quality=quality,
        )

    # ── IV rank ───────────────────────────────────────────

    def get_iv_rank(self, symbol: str, lookback_days: Optional[int] = None) -> Optional[float]:
        """IV Rank = (current IV − 52w low) / (52w high − 52w low) × 100.

        Uses reqHistoricalData with whatToShow='OPTION_IMPLIED_VOLATILITY' on
        the underlying stock. Falls back to None on failure.
        """
        if lookback_days is None:
            lookback_days = self._iv_lookback_days

        cache_key = f"iv_rank|{symbol}|{lookback_days}"
        cached = self._iv_cache.get(cache_key)
        if cached and (time.time() - cached[0]) < max(self._cache_ttl * 10, 300):
            return cached[1]

        try:
            stock = Stock(symbol, "SMART", "USD")
            self.client.qualify_contract(stock)
            bars = self.client.ib.reqHistoricalData(
                stock,
                endDateTime="",
                durationStr=f"{lookback_days} D",
                barSizeSetting="1 day",
                whatToShow="OPTION_IMPLIED_VOLATILITY",
                useRTH=True,
                formatDate=1,
            )
        except Exception as e:
            log.warning("IV Rank fetch failed for %s: %s", symbol, e)
            self._iv_cache[cache_key] = (time.time(), None)
            return None

        closes = [b.close for b in bars if getattr(b, "close", None) and b.close > 0]
        if len(closes) < 20:
            self._iv_cache[cache_key] = (time.time(), None)
            return None

        current = closes[-1]
        lo = min(closes)
        hi = max(closes)
        if hi <= lo:
            self._iv_cache[cache_key] = (time.time(), None)
            return None

        rank = (current - lo) / (hi - lo) * 100.0
        rank = max(0.0, min(100.0, round(rank, 2)))
        self._iv_cache[cache_key] = (time.time(), rank)
        return rank

    def _get_historical_vol(self, symbol: str) -> Optional[float]:
        try:
            stock = Stock(symbol, "SMART", "USD")
            self.client.qualify_contract(stock)
            bars = self.client.ib.reqHistoricalData(
                stock,
                endDateTime="",
                durationStr="30 D",
                barSizeSetting="1 day",
                whatToShow="HISTORICAL_VOLATILITY",
                useRTH=True,
                formatDate=1,
            )
            if bars and bars[-1].close:
                return float(bars[-1].close)
        except Exception as e:
            log.debug("HV fetch failed for %s: %s", symbol, e)
        return None

    # ── helpers ───────────────────────────────────────────

    def _fetch_underlying_price(self, symbol: str, exchange: str) -> float:
        try:
            stock = Stock(symbol, exchange, "USD")
            self.client.qualify_contract(stock)
            ticker = self.client.ib.reqMktData(stock, "", snapshot=True, regulatorySnapshot=False)
            self.client.ib.sleep(1.0)
            for candidate in (ticker.last, ticker.close, ticker.marketPrice()):
                p = _sanitize_price(candidate)
                if p is not None:
                    return p
        except Exception as e:
            log.warning("Underlying price fetch failed for %s: %s", symbol, e)
        return 0.0

    def _select_strikes_in_range(
        self,
        symbol: str,
        expiration: str,
        underlying_price: float,
        strike_range_pct: float,
        exchange: str,
    ) -> List[float]:
        """Select published strikes within ±range of underlying price."""
        try:
            chains = self.client.option_chains(symbol)
        except Exception as e:
            log.error("option_chains failed for %s: %s", symbol, e)
            return []

        all_strikes: List[float] = []
        for chain in chains:
            if expiration in chain.expirations:
                all_strikes.extend(chain.strikes)

        if not all_strikes or underlying_price <= 0:
            return sorted(set(all_strikes))

        lo = underlying_price * (1.0 - strike_range_pct)
        hi = underlying_price * (1.0 + strike_range_pct)
        filtered = sorted({s for s in all_strikes if lo <= s <= hi})
        return filtered


def _avg(a, b):
    if a is None or b is None:
        return None
    try:
        return (float(a) + float(b)) / 2.0
    except (TypeError, ValueError):
        return None
