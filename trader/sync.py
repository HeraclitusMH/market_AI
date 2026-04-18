"""Sync positions, orders, and fills from IBKR to local DB."""
from __future__ import annotations

import json
from typing import Optional

from common.db import get_db
from common.logging import get_logger
from common.models import Position, Order, Fill
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
    """Sync open positions from IBKR."""
    if client is None:
        client = get_ibkr_client()

    try:
        ibkr_positions = client.positions()
        now = utcnow()

        with get_db() as db:
            # Clear existing and re-populate
            db.query(Position).delete()
            for pos in ibkr_positions:
                contract = pos.contract
                symbol = contract.localSymbol or contract.symbol
                instrument = "stock"
                if contract.secType == "OPT":
                    instrument = "option"
                elif contract.secType == "BAG":
                    instrument = "combo"

                db.add(Position(
                    symbol=symbol,
                    quantity=int(pos.position),
                    avg_cost=float(pos.avgCost),
                    market_price=float(pos.marketPrice) if pos.marketPrice else 0.0,
                    market_value=float(pos.marketValue) if pos.marketValue else 0.0,
                    unrealized_pnl=float(pos.unrealizedPNL) if pos.unrealizedPNL else 0.0,
                    instrument=instrument,
                    updated_at=now,
                ))

        log.debug("Synced %d positions.", len(ibkr_positions))
    except Exception as e:
        log.error("Failed to sync positions: %s", e)


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
