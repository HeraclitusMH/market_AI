"""Sync positions, orders, and fills from IBKR to local DB."""
from __future__ import annotations

import json
from collections import Counter
from typing import Optional

from sqlalchemy.orm import Session

from common.db import get_db
from common.logging import get_logger
from common.models import Position, Order, Fill, TradeManagement
from common.time import utcnow
from trader.ibkr_client import IBKRClient, get_ibkr_client
from trader.risk import record_equity_snapshot, log_event

log = get_logger(__name__)


def sync_account(client: IBKRClient | None = None) -> None:
    """Sync account summary / equity snapshot from IBKR."""
    if client is None:
        client = get_ibkr_client()

    try:
        vals = client.account_values()
        net_liq = float(vals.get("NetLiquidation", 0))
        cash = float(vals.get("TotalCashValue", 0))
        unrealized = float(vals.get("UnrealizedPnL", 0))
        realized = float(vals.get("RealizedPnL", 0))

        dd = record_equity_snapshot(net_liq, cash, unrealized, realized)
        log.debug("Equity sync: NLV=$%.2f cash=$%.2f DD=%.1f%%", net_liq, cash, dd)
    except Exception as e:
        log.error("Failed to sync account: %s", e)


def sync_positions(client: IBKRClient | None = None) -> None:
    """Sync open positions from IBKR and reconcile portfolio attribution."""
    if client is None:
        client = get_ibkr_client()

    try:
        ibkr_positions = client.positions()
    except Exception as e:
        log.error("Failed to fetch positions from IBKR: %s", e)
        return

    try:
        with get_db() as db:
            summary = _sync_positions_in_session(db, ibkr_positions)

        log.info(
            "[SYNC] Positions synced: %d attributed, %d unattributed, %d total",
            summary["attributed"],
            summary["unattributed"],
            summary["total"],
        )
        log_event(
            "INFO",
            "sync_positions_reconciled",
            "Positions synced and reconciled",
            {
                "total": summary["total"],
                "equity_swing": summary["by_portfolio"].get("equity_swing", 0),
                "options_swing": summary["by_portfolio"].get("options_swing", 0),
                "unattributed": summary["by_portfolio"].get("unattributed", 0),
                "symbols_unattributed": summary["symbols_unattributed"],
            },
        )
        if summary["unattributed"] > 0:
            log_event(
                "WARN",
                "sync_unattributed_positions",
                f"{summary['unattributed']} positions could not be attributed to a portfolio",
                {"symbols": summary["symbols_unattributed"]},
            )
    except Exception as e:
        log.error("Failed to sync positions: %s", e)


def _sync_positions_in_session(db: Session, ibkr_positions: list) -> dict:
    now = utcnow()
    attribution_map = _build_attribution_map(db)

    db.query(Position).delete()

    by_portfolio: Counter[str] = Counter()
    symbols_unattributed: list[str] = []
    total = 0

    for pos in ibkr_positions:
        quantity = int(pos.position)
        if quantity == 0:
            continue

        contract = pos.contract
        symbol = getattr(contract, "symbol", "") or getattr(contract, "localSymbol", "")
        instrument = _classify_instrument(contract)
        portfolio_id = _reconcile_portfolio_id(
            symbol=symbol,
            quantity=quantity,
            instrument=instrument,
            attribution_map=attribution_map,
        )

        total += 1
        by_portfolio[portfolio_id] += 1
        if portfolio_id == "unattributed":
            symbols_unattributed.append(symbol)
            log.warning(
                "[SYNC] Position %s (qty=%s, type=%s) could not be attributed to any bot portfolio",
                symbol,
                quantity,
                instrument,
            )

        db.add(Position(
            symbol=symbol,
            quantity=quantity,
            avg_cost=float(pos.avgCost),
            market_price=float(pos.marketPrice) if pos.marketPrice else 0.0,
            market_value=float(pos.marketValue) if pos.marketValue else 0.0,
            unrealized_pnl=float(pos.unrealizedPNL) if pos.unrealizedPNL else 0.0,
            instrument=instrument,
            portfolio_id=portfolio_id,
            updated_at=now,
        ))

    attributed = total - by_portfolio.get("unattributed", 0)
    return {
        "total": total,
        "attributed": attributed,
        "unattributed": by_portfolio.get("unattributed", 0),
        "by_portfolio": dict(by_portfolio),
        "symbols_unattributed": sorted(set(symbols_unattributed)),
    }


def _build_attribution_map(db: Session) -> dict[str, dict[str, list]]:
    attribution: dict[str, dict[str, list]] = {}

    for tm in db.query(TradeManagement).all():
        attribution.setdefault(tm.symbol, {"trade_managements": [], "latest_orders": []})
        attribution[tm.symbol]["trade_managements"].append(tm)

    latest_orders = (
        db.query(Order)
        .filter(Order.status.in_(["filled", "submitted", "pending_approval"]))
        .order_by(Order.timestamp.desc(), Order.id.desc())
        .limit(500)
        .all()
    )
    seen_per_symbol: dict[str, int] = {}
    for order in latest_orders:
        count = seen_per_symbol.get(order.symbol, 0)
        if count >= 5:
            continue
        attribution.setdefault(order.symbol, {"trade_managements": [], "latest_orders": []})
        attribution[order.symbol]["latest_orders"].append(order)
        seen_per_symbol[order.symbol] = count + 1

    return attribution


def _reconcile_portfolio_id(
    symbol: str,
    quantity: int,
    instrument: str,
    attribution_map: dict[str, dict[str, list]],
) -> str:
    data = attribution_map.get(symbol)
    if data is None:
        return "unattributed"

    direction = "long" if quantity > 0 else "short"

    for tm in data.get("trade_managements", []):
        if (
            tm.portfolio_id
            and _direction_matches(tm.direction, direction)
            and _instrument_matches(tm.instrument_type, instrument)
        ):
            return tm.portfolio_id

    for order in data.get("latest_orders", []):
        if (
            order.portfolio_id
            and _direction_matches(order.direction, direction)
            and _portfolio_matches_instrument(order.portfolio_id, instrument)
        ):
            return order.portfolio_id

    for order in data.get("latest_orders", []):
        if (
            order.portfolio_id
            and _is_opening_direction(order.direction)
            and _portfolio_matches_instrument(order.portfolio_id, instrument)
        ):
            return order.portfolio_id

    return "unattributed"


def _direction_matches(order_direction: str, position_direction: str) -> bool:
    if position_direction == "long":
        return order_direction == "long"
    if position_direction == "short":
        return order_direction in ("short", "bearish")
    return False


def _is_opening_direction(order_direction: str) -> bool:
    return order_direction in ("long", "short", "bearish")


def _instrument_matches(managed_instrument: str, position_instrument: str) -> bool:
    if managed_instrument == "equity" and position_instrument == "stock":
        return True
    if managed_instrument == "debit_spread" and position_instrument in ("option", "combo"):
        return True
    return False


def _portfolio_matches_instrument(portfolio_id: str, instrument: str) -> bool:
    if portfolio_id == "equity_swing":
        return instrument == "stock"
    if portfolio_id == "options_swing":
        return instrument in ("option", "combo")
    return False


def _classify_instrument(contract) -> str:
    sec_type = getattr(contract, "secType", "")
    if sec_type == "STK":
        return "stock"
    if sec_type == "OPT":
        return "option"
    if sec_type == "BAG":
        return "combo"
    if sec_type == "FUT":
        return "future"
    if sec_type == "CASH":
        return "forex"
    return "other"


def sync_orders(client: IBKRClient | None = None) -> None:
    """Sync open order statuses from IBKR."""
    if client is None:
        client = get_ibkr_client()

    try:
        trades = client.open_trades()
        with get_db() as db:
            for trade in trades:
                oid = trade.order.orderId
                status = trade.orderStatus.status

                order = db.query(Order).filter(Order.ibkr_order_id == oid).first()
                if order:
                    order.status = status.lower()
                    order.updated_at = utcnow()

                # Record fills
                for fill_evt in trade.fills:
                    existing = db.query(Fill).filter(
                        Fill.order_id == (order.id if order else 0),
                        Fill.timestamp == fill_evt.time,
                    ).first()
                    if not existing and order:
                        db.add(Fill(
                            order_id=order.id,
                            timestamp=fill_evt.time,
                            symbol=fill_evt.contract.symbol,
                            quantity=int(fill_evt.execution.shares),
                            price=float(fill_evt.execution.price),
                            commission=float(fill_evt.commissionReport.commission)
                            if fill_evt.commissionReport else 0.0,
                            payload_json=json.dumps({
                                "exec_id": fill_evt.execution.execId,
                                "side": fill_evt.execution.side,
                            }),
                        ))

        log.debug("Synced %d open trades.", len(trades))
    except Exception as e:
        log.error("Failed to sync orders: %s", e)


def full_sync(client: IBKRClient | None = None) -> None:
    """Run all sync operations."""
    sync_account(client)
    sync_positions(client)
    sync_orders(client)
