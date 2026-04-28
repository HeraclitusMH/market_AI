"""IBKR connection manager using ib_insync."""
from __future__ import annotations

import asyncio
from typing import Dict, List, Optional

from ib_insync import IB, Contract, Stock, Option, ComboLeg, Order as IBOrder, TagValue
from ib_insync import util as ib_util

from common.config import get_config
from common.logging import get_logger

log = get_logger(__name__)


class IBKRClient:
    """Wrapper around ib_insync.IB with reconnect logic."""

    def __init__(self) -> None:
        self.ib = IB()
        self.cfg = get_config().ibkr
        self._connected = False

    @property
    def connected(self) -> bool:
        return self.ib.isConnected()

    def connect(self) -> None:
        if self.connected:
            return
        log.info("Connecting to IBKR %s:%s clientId=%s", self.cfg.host, self.cfg.port, self.cfg.client_id)
        self.ib.connect(self.cfg.host, self.cfg.port, clientId=self.cfg.client_id, readonly=False)
        self._connected = True
        log.info("Connected to IBKR. Accounts: %s", self.ib.managedAccounts())
        self._apply_market_data_type()

    def _apply_market_data_type(self) -> None:
        """Tell IBKR to send delayed quotes when live aren't subscribed.

        1=Live, 2=Frozen, 3=Delayed, 4=Delayed-Frozen. Without this call,
        accounts without US market-data subscriptions get errors 10089/354
        on every reqMktData and a flood of "no security definition" (200)
        downstream because option strikes are picked from a NaN underlying
        price.
        """
        mdt = int(getattr(self.cfg, "market_data_type", 3) or 3)
        if mdt not in (1, 2, 3, 4):
            log.warning("Invalid ibkr.market_data_type=%s; falling back to 3 (delayed).", mdt)
            mdt = 3
        try:
            self.ib.reqMarketDataType(mdt)
            log.info("IBKR market data type set to %d (1=live,2=frozen,3=delayed,4=delayed-frozen).", mdt)
        except Exception as e:
            log.warning("reqMarketDataType(%d) failed: %s", mdt, e)

    def disconnect(self) -> None:
        if self.connected:
            self.ib.disconnect()
            self._connected = False
            log.info("Disconnected from IBKR.")

    def ensure_connected(self) -> None:
        if not self.connected:
            log.warning("IBKR disconnected — reconnecting...")
            self.connect()

    def account_id(self) -> str:
        acct = self.cfg.account
        if acct:
            return acct
        accounts = self.ib.managedAccounts()
        return accounts[0] if accounts else ""

    def account_summary(self) -> Dict[str, str]:
        self.ensure_connected()
        summary = self.ib.accountSummary(self.account_id())
        return {item.tag: item.value for item in summary}

    def account_values(self) -> Dict[str, str]:
        self.ensure_connected()
        vals = self.ib.accountValues(self.account_id())
        return {v.tag: v.value for v in vals if v.currency in ("USD", "BASE", "")}

    def positions(self) -> list:
        self.ensure_connected()
        return self.ib.positions(self.account_id())

    def open_orders(self) -> list:
        self.ensure_connected()
        return self.ib.openOrders()

    def open_trades(self) -> list:
        self.ensure_connected()
        return self.ib.openTrades()

    def historical_bars(
        self,
        contract: Contract,
        duration: str = "60 D",
        bar_size: str = "1 day",
        what_to_show: str = "TRADES",
        use_rth: bool = True,
    ) -> list:
        self.ensure_connected()
        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
            useRTH=use_rth,
            formatDate=1,
        )
        return bars

    def fundamental_data(self, contract: Contract, report_type: str = "ReportSnapshot") -> str:
        """Request IBKR fundamental data XML for a qualified contract."""
        self.ensure_connected()
        qualified = self.qualify_contract(contract)
        return self.ib.reqFundamentalData(qualified, report_type, [])

    def qualify_contract(self, contract: Contract) -> Contract:
        self.ensure_connected()
        qualified = self.ib.qualifyContracts(contract)
        return qualified[0] if qualified else contract

    def option_chains(self, symbol: str) -> list:
        self.ensure_connected()
        stock = Stock(symbol, "SMART", "USD")
        self.qualify_contract(stock)
        chains = self.ib.reqSecDefOptParams(stock.symbol, "", stock.secType, stock.conId)
        return chains

    def option_chain_data(self, symbol: str, exchange: str, expiry: str, strikes: list) -> list:
        """Request market data for specific option contracts."""
        self.ensure_connected()
        contracts = []
        for strike in strikes:
            for right in ("C", "P"):
                opt = Option(symbol, expiry, strike, right, exchange)
                contracts.append(opt)
        qualified = self.ib.qualifyContracts(*contracts)
        tickers = []
        for c in qualified:
            ticker = self.ib.reqMktData(c, "", snapshot=True)
            tickers.append(ticker)
        self.ib.sleep(2)  # wait for data
        return tickers

    def place_order(self, contract: Contract, order: IBOrder) -> object:
        self.ensure_connected()
        trade = self.ib.placeOrder(contract, order)
        log.info("Order placed: %s %s", contract.symbol, order.action)
        return trade

    def cancel_order(self, order: IBOrder) -> None:
        self.ensure_connected()
        self.ib.cancelOrder(order)

    def cancel_all_orders(self) -> None:
        self.ensure_connected()
        self.ib.reqGlobalCancel()

    def sleep(self, seconds: float = 0.1) -> None:
        self.ib.sleep(seconds)


# Module-level singleton
_client: Optional[IBKRClient] = None


def get_ibkr_client() -> IBKRClient:
    global _client
    if _client is None:
        _client = IBKRClient()
    return _client
