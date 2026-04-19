"""Universe management — seed tickers, filter by liquidity, IBKR contract verification."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from common.config import get_config
from common.db import get_db
from common.logging import get_logger
from common.models import Universe, ContractVerificationCache
from common.time import utcnow
from trader.sentiment.scoring import get_recent_ticker_scores

log = get_logger(__name__)


@dataclass
class UniverseItem:
    symbol: str
    sector: str
    name: str
    type: str          # "STK" or "ETF"
    sources: List[str] = field(default_factory=list)
    verified: bool = True
    conid: Optional[int] = None

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


# Lookup table for human-readable names (used in UniverseItem)
_NAMES: dict[str, str] = {sym: sym for sym, _, _ in SEED_TICKERS}
_NAMES.update({
    "SPY": "SPDR S&P 500 ETF", "QQQ": "Invesco QQQ Trust",
    "IWM": "iShares Russell 2000 ETF", "DIA": "SPDR Dow Jones ETF",
    "AAPL": "Apple Inc", "MSFT": "Microsoft Corp", "AMZN": "Amazon.com Inc",
    "GOOGL": "Alphabet Inc", "META": "Meta Platforms Inc", "NVDA": "NVIDIA Corp",
    "TSLA": "Tesla Inc", "JPM": "JPMorgan Chase", "V": "Visa Inc",
    "JNJ": "Johnson & Johnson", "UNH": "UnitedHealth Group",
})


def verify_contract(symbol: str, client=None) -> dict:
    """Verify a symbol is a real US-listed contract via IBKR, with 24h caching.

    Returns dict: {verified, conid, primary_exchange, reason}
    Never trades if IBKR unavailable — returns verified=True for existing Universe rows
    so the core list is always usable without live connection.
    """
    cfg = get_config()
    cache_hours = cfg.ranking.contract_cache_hours
    cutoff = utcnow() - timedelta(hours=cache_hours)

    # Check cache first
    with get_db() as db:
        cached = db.query(ContractVerificationCache).filter(
            ContractVerificationCache.symbol == symbol
        ).first()
        if cached and cached.checked_at >= cutoff.replace(tzinfo=None):
            return {
                "verified": cached.verified,
                "conid": cached.contract_conid,
                "primary_exchange": cached.primary_exchange,
                "reason": cached.reason,
            }

    if client is None:
        # No IBKR connection — cannot verify discovered tickers; core tickers trusted
        return {"verified": False, "conid": None, "primary_exchange": None,
                "reason": "no_ibkr_connection"}

    try:
        from ib_insync import Stock
        stock = Stock(symbol, "SMART", "USD")
        details = client.ib.reqContractDetails(stock)
        if not details:
            _save_verification(symbol, False, None, None, "no_contract_details")
            return {"verified": False, "conid": None, "primary_exchange": None,
                    "reason": "no_contract_details"}

        # Reject OTC / Pink Sheets / non-USD
        d = details[0]
        exchange = getattr(d, "primaryExch", "") or ""
        currency = getattr(d.contract, "currency", "USD")
        conid = getattr(d.contract, "conId", None)

        if currency != "USD":
            _save_verification(symbol, False, conid, exchange, f"currency_{currency}")
            return {"verified": False, "conid": conid, "primary_exchange": exchange,
                    "reason": f"currency_{currency}"}

        if exchange.upper() in ("PINK", "OTC", "OTCBB", "GREY"):
            _save_verification(symbol, False, conid, exchange, f"otc_{exchange}")
            return {"verified": False, "conid": conid, "primary_exchange": exchange,
                    "reason": f"otc_{exchange}"}

        _save_verification(symbol, True, conid, exchange, "ok")
        return {"verified": True, "conid": conid, "primary_exchange": exchange, "reason": "ok"}

    except Exception as e:
        log.warning("Contract verification failed for %s: %s", symbol, e)
        _save_verification(symbol, False, None, None, f"error: {e}")
        return {"verified": False, "conid": None, "primary_exchange": None, "reason": str(e)}


def _save_verification(
    symbol: str, verified: bool, conid: Optional[int],
    exchange: Optional[str], reason: str,
) -> None:
    with get_db() as db:
        row = db.query(ContractVerificationCache).filter(
            ContractVerificationCache.symbol == symbol
        ).first()
        if row:
            row.verified = verified
            row.checked_at = utcnow().replace(tzinfo=None)
            row.contract_conid = conid
            row.primary_exchange = exchange
            row.reason = reason
        else:
            db.add(ContractVerificationCache(
                symbol=symbol,
                verified=verified,
                checked_at=utcnow().replace(tzinfo=None),
                contract_conid=conid,
                primary_exchange=exchange,
                reason=reason,
            ))


def get_verified_universe(client=None) -> List[UniverseItem]:
    """Merge core universe, always-tradable ETFs, and RSS-discovered tickers.

    Core and ETF tickers are trusted without IBKR verification.
    Discovered tickers (from ticker sentiment) require IBKR verification.
    """
    # Build set of core items from DB Universe table (seeded from SEED_TICKERS)
    core_syms: dict[str, UniverseItem] = {}
    with get_db() as db:
        rows = db.query(Universe).filter(Universe.active == True).all()
        for r in rows:
            core_syms[r.symbol] = UniverseItem(
                symbol=r.symbol,
                sector=r.sector,
                name=_NAMES.get(r.symbol, r.symbol),
                type=r.type,
                sources=["core"],
                verified=True,
                conid=None,
            )

    # Always-tradable broad ETFs (guaranteed present if Universe seeded)
    always_etfs = {"SPY", "QQQ", "IWM", "DIA"}
    for sym in always_etfs:
        if sym not in core_syms:
            sector = "Broad Market"
            core_syms[sym] = UniverseItem(
                symbol=sym, sector=sector, name=_NAMES.get(sym, sym),
                type="ETF", sources=["etf"], verified=True,
            )
        else:
            if "etf" not in core_syms[sym].sources:
                core_syms[sym].sources.append("etf")

    # Add RSS-discovered tickers not already in core (import at top of module)
    try:
        discovered = get_recent_ticker_scores(hours=72, limit=200)
    except Exception as e:
        log.warning("Could not load discovered tickers: %s", e)
        discovered = []

    for rec in discovered:
        sym = rec.symbol.upper()
        if sym in core_syms:
            if "rss_discovered" not in core_syms[sym].sources:
                core_syms[sym].sources.append("rss_discovered")
            continue

        # Verify via IBKR
        result = verify_contract(sym, client)
        if not result["verified"]:
            log.debug("Skipping discovered ticker %s: %s", sym, result["reason"])
            continue

        core_syms[sym] = UniverseItem(
            symbol=sym,
            sector="Unknown",
            name=sym,
            type="STK",
            sources=["rss_discovered"],
            verified=True,
            conid=result["conid"],
        )

    return list(core_syms.values())
