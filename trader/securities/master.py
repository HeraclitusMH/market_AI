"""Security master import pipeline and IBKR verification.

Entry points:
    import_csv(path, db, *, verify_ibkr=False, refresh_aliases=True)
    verify_security(symbol, client) -> dict
    check_options_eligibility(symbol, client) -> bool
    refresh_liquidity(symbol, client, lookback=20) -> float | None
    load_manual_overrides(path, db)
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from common.config import get_config
from common.db import get_db
from common.logging import get_logger
from common.models import SecurityAlias, SecurityMaster
from common.time import utcnow
from trader.securities.normalize import generate_aliases

log = get_logger(__name__)


# ── CSV import ───────────────────────────────────────────────────────

def import_csv(
    path: str | Path,
    *,
    verify_ibkr: bool = False,
    refresh_aliases: bool = True,
    client=None,
) -> dict:
    """Upsert security_master from a CSV file and optionally regenerate aliases.

    CSV columns (required): symbol, name, exchange
    CSV columns (optional): security_type, currency

    Returns a summary dict.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Security master CSV not found: {path}")

    cfg = get_config().securities
    added = updated = skipped = alias_count = 0
    now = utcnow().replace(tzinfo=None)

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    with get_db() as db:
        for row in rows:
            symbol = (row.get("symbol") or "").strip().upper()
            name = (row.get("name") or "").strip()
            exchange = (row.get("exchange") or "").strip().upper()

            if not symbol or not name or not exchange:
                skipped += 1
                continue

            if exchange not in cfg.allowed_exchanges:
                skipped += 1
                continue

            security_type = (row.get("security_type") or "STK").strip().upper()
            currency = (row.get("currency") or "USD").strip().upper()

            existing = db.query(SecurityMaster).filter(
                SecurityMaster.symbol == symbol
            ).first()

            if existing:
                existing.name = name
                existing.exchange = exchange
                existing.security_type = security_type
                existing.currency = currency
                existing.updated_at = now
                updated += 1
            else:
                db.add(SecurityMaster(
                    symbol=symbol,
                    name=name,
                    exchange=exchange,
                    security_type=security_type,
                    currency=currency,
                    active=True,
                    options_eligible=False,
                    updated_at=now,
                ))
                added += 1

            if refresh_aliases:
                alias_count += _upsert_aliases(db, symbol, name, now)

        # IBKR verification pass (optional — requires live connection)
        if verify_ibkr and client is not None:
            _verify_all_in_db(db, client, cfg, now)

    log.info(
        "Security master import: added=%d updated=%d skipped=%d aliases=%d",
        added, updated, skipped, alias_count,
    )
    return {
        "added": added,
        "updated": updated,
        "skipped": skipped,
        "aliases_written": alias_count,
    }


def _upsert_aliases(db, symbol: str, name: str, now: datetime) -> int:
    count = 0
    for alias, alias_type, priority in generate_aliases(symbol, name):
        if not alias:
            continue
        existing = db.query(SecurityAlias).filter(
            SecurityAlias.alias == alias
        ).first()
        if existing:
            # Only update if this symbol has higher priority (lower number = higher)
            if priority < existing.priority:
                existing.symbol = symbol
                existing.alias_type = alias_type
                existing.priority = priority
        else:
            db.add(SecurityAlias(
                alias=alias,
                symbol=symbol,
                alias_type=alias_type,
                priority=priority,
                created_at=now,
            ))
            count += 1
    return count


def _verify_all_in_db(db, client, cfg, now: datetime) -> None:
    rows = db.query(SecurityMaster).filter(SecurityMaster.active == True).all()
    for row in rows:
        result = verify_security(row.symbol, client)
        if result["verified"]:
            row.ibkr_conid = result.get("conid")
            row.updated_at = now
        else:
            # Mark inactive if IBKR can't find the contract
            row.active = False
            row.updated_at = now
        log.debug("IBKR verify %s: %s", row.symbol, result["reason"])


# ── IBKR verification ────────────────────────────────────────────────

def verify_security(symbol: str, client) -> dict:
    """Verify a security via IBKR contract lookup.

    Returns: {verified: bool, conid: int|None, exchange: str|None, reason: str}
    Reuses ContractVerificationCache from trader.universe to avoid double-fetching.
    """
    from trader.universe import verify_contract
    cfg = get_config().securities
    result = verify_contract(symbol, client)

    # Additional exchange filter: reject if not in allowed_exchanges
    exch = (result.get("primary_exchange") or "").upper()
    if result["verified"] and exch and exch not in [e.upper() for e in cfg.allowed_exchanges]:
        return {
            "verified": False,
            "conid": result.get("conid"),
            "exchange": exch,
            "reason": f"exchange_not_allowed:{exch}",
        }

    return {
        "verified": result["verified"],
        "conid": result.get("conid"),
        "exchange": exch,
        "reason": result.get("reason", ""),
    }


def check_options_eligibility(symbol: str, client) -> bool:
    """Return True if IBKR can provide an option chain for the symbol."""
    if client is None:
        return False
    try:
        chains = client.option_chains(symbol)
        return bool(chains)
    except Exception as e:
        log.debug("Options eligibility check failed for %s: %s", symbol, e)
        return False


def refresh_liquidity(symbol: str, client, lookback: int = 20) -> Optional[float]:
    """Compute avg_dollar_volume_20d via IBKR historical bars.

    Returns the computed value on success, None on failure.
    """
    if client is None:
        return None
    try:
        from ib_insync import Stock
        from trader.market_data import fetch_bars
        df = fetch_bars(symbol, "1D", client)
        if df.empty or len(df) < 5:
            return None
        recent = df.tail(lookback)
        adv = float((recent["close"] * recent["volume"]).mean())
        with get_db() as db:
            row = db.query(SecurityMaster).filter(
                SecurityMaster.symbol == symbol
            ).first()
            if row:
                row.avg_dollar_volume_20d = adv
                row.updated_at = utcnow().replace(tzinfo=None)
        return adv
    except Exception as e:
        log.debug("Liquidity refresh failed for %s: %s", symbol, e)
        return None


# ── Manual alias overrides ───────────────────────────────────────────

def load_manual_overrides(path: str | Path) -> dict:
    """Load manual_alias_overrides.csv: alias,symbol columns.

    Example row: "health care", UNH  → maps "health care" → UNH at priority 1.
    Returns summary dict.
    """
    path = Path(path)
    if not path.exists():
        log.debug("No manual alias overrides file at %s", path)
        return {"loaded": 0}

    now = utcnow().replace(tzinfo=None)
    loaded = 0
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        with get_db() as db:
            for row in reader:
                alias = (row.get("alias") or "").strip().lower()
                symbol = (row.get("symbol") or "").strip().upper()
                if not alias or not symbol:
                    continue
                existing = db.query(SecurityAlias).filter(
                    SecurityAlias.alias == alias
                ).first()
                if existing:
                    existing.symbol = symbol
                    existing.alias_type = "manual"
                    existing.priority = 1
                else:
                    db.add(SecurityAlias(
                        alias=alias,
                        symbol=symbol,
                        alias_type="manual",
                        priority=1,
                        created_at=now,
                    ))
                loaded += 1

    log.info("Loaded %d manual alias overrides from %s", loaded, path)
    return {"loaded": loaded}
