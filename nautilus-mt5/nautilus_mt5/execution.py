"""Orders, positions and reconciliation against the terminal.

**Reconciliation is the part that matters here and the part most likely to be
got wrong.** `judge-the-desk-on-the-broker-not-the-journal` is the rule: the
broker's view is authoritative and ours is a record. `DEFAULT_MAGIC` is
777_701, and being off by one relabels every strategy as its neighbour - so
the magic is how this client decides which positions are the desk's at all.
"""

from __future__ import annotations

import asyncio
from typing import Any

from nautilus_trader.execution.reports import FillReport, OrderStatusReport, PositionStatusReport
from nautilus_trader.live.execution_client import LiveExecutionClient
from nautilus_trader.model.enums import (
    AccountType,
    LiquiditySide,
    OmsType,
    OrderSide,
    OrderStatus,
    OrderType,
)
from nautilus_trader.model.identifiers import (
    AccountId,
    ClientId,
    ClientOrderId,
    InstrumentId,
    PositionId,
    Symbol,
    TradeId,
    Venue,
    VenueOrderId,
)
from nautilus_trader.model.objects import Money, Price, Quantity

from .bridge import Bridge
from .config import MT5ExecClientConfig
from .providers import MT5InstrumentProvider

#: MT5 position types. 0 buy, 1 sell - and nothing else is a position.
_SIDES = {0: OrderSide.BUY, 1: OrderSide.SELL}


class MT5ExecutionClient(LiveExecutionClient):
    """The order side of the venue.

    **Net, not hedging.** MT5 accounts can be either and the distinction is
    not cosmetic: on a netting account a second buy adds to one position and on
    a hedging account it opens a second. `OmsType.NETTING` is declared here
    because that is what the Deriv account is; a hedging account would need
    `HEDGING` and would change what `_generate_position_status_reports` means.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        client_id: ClientId,
        venue: Venue,
        msgbus: Any,
        cache: Any,
        clock: Any,
        instrument_provider: MT5InstrumentProvider,
        config: MT5ExecClientConfig,
    ) -> None:
        super().__init__(
            loop=loop,
            client_id=client_id,
            venue=venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            base_currency=None,
            instrument_provider=instrument_provider,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            config=config,
        )
        self._cfg = config
        self._bridge = Bridge(config.base_url, config.api_key, timeout=config.request_timeout)
        self._venue = venue

    # ──── lifecycle ────
    async def _connect(self) -> None:
        await self._bridge.open()
        account = await self._bridge.account()
        if not account:
            raise ConnectionError("the bridge returned no account - is the terminal logged in?")
        # **The venue answering is what "connected" means.** Three deploys on
        # 2026-10-09 were built, tagged, health-checked and reported up while
        # running none of the code they claimed, because every check read our
        # own side. An account query is the broker's answer.
        login = account.get("login") or account.get("account")
        self._set_account_id(AccountId(f"{self._venue.value}-{login}"))
        self._log.info(f"account {login}, equity {account.get('equity')}")

    async def _disconnect(self) -> None:
        await self._bridge.close()

    # ──── reconciliation ────
    async def _generate_position_status_reports(
        self, command: Any = None
    ) -> list[PositionStatusReport]:
        reports = []
        for row in await self._bridge.positions():
            if int(row.get("magic") or 0) != self._cfg.magic:
                continue  # not ours; another strategy or a human
            side = _SIDES.get(int(row.get("type") or 0))
            if side is None:
                continue
            instrument_id = InstrumentId(Symbol(str(row.get("symbol"))), self._venue)
            instrument = self._instrument_provider.find(instrument_id)
            if instrument is None:
                continue
            now = self._clock.timestamp_ns()
            reports.append(
                PositionStatusReport(
                    account_id=self.account_id,
                    instrument_id=instrument_id,
                    position_side=side.to_position_side(),
                    quantity=Quantity(float(row.get("volume") or 0.0), instrument.size_precision),
                    report_id=self._uuid4(),
                    ts_last=now,
                    ts_init=now,
                    venue_position_id=PositionId(str(row.get("ticket"))),
                )
            )
        return reports

    async def _generate_order_status_reports(self, command: Any = None) -> list[OrderStatusReport]:
        """Resting orders only. A filled order is a position, reported above."""
        reports = []
        for row in await self._bridge.pending():
            if int(row.get("magic") or 0) != self._cfg.magic:
                continue
            instrument_id = InstrumentId(Symbol(str(row.get("symbol"))), self._venue)
            instrument = self._instrument_provider.find(instrument_id)
            if instrument is None:
                continue
            now = self._clock.timestamp_ns()
            reports.append(
                OrderStatusReport(
                    account_id=self.account_id,
                    instrument_id=instrument_id,
                    venue_order_id=VenueOrderId(str(row.get("ticket"))),
                    order_side=_SIDES.get(int(row.get("type") or 0) % 2, OrderSide.BUY),
                    order_type=OrderType.LIMIT,
                    time_in_force=self._default_tif(),
                    order_status=OrderStatus.ACCEPTED,
                    quantity=Quantity(
                        float(row.get("volume_current") or row.get("volume") or 0.0),
                        instrument.size_precision,
                    ),
                    filled_qty=Quantity(0, instrument.size_precision),
                    report_id=self._uuid4(),
                    ts_accepted=now,
                    ts_last=now,
                    ts_init=now,
                    price=Price(
                        float(row.get("price_open") or 0.0),
                        instrument.price_precision,
                    ),
                )
            )
        return reports

    async def _generate_fill_reports(self, command: Any = None) -> list[FillReport]:
        """From `/history/deals`, filtered to this desk's magic."""
        now_s = int(self._clock.timestamp_ns() / 1e9)
        reports = []
        for row in await self._bridge.deals(now_s - 86_400, now_s + 60):
            if int(row.get("magic") or 0) != self._cfg.magic:
                continue
            instrument_id = InstrumentId(Symbol(str(row.get("symbol"))), self._venue)
            instrument = self._instrument_provider.find(instrument_id)
            if instrument is None:
                continue
            now = self._clock.timestamp_ns()
            reports.append(
                FillReport(
                    account_id=self.account_id,
                    instrument_id=instrument_id,
                    venue_order_id=VenueOrderId(str(row.get("order") or row.get("ticket"))),
                    trade_id=TradeId(str(row.get("ticket"))),
                    order_side=_SIDES.get(int(row.get("type") or 0), OrderSide.BUY),
                    last_qty=Quantity(float(row.get("volume") or 0.0), instrument.size_precision),
                    last_px=Price(float(row.get("price") or 0.0), instrument.price_precision),
                    commission=Money(
                        float(row.get("commission") or 0.0), instrument.quote_currency
                    ),
                    liquidity_side=LiquiditySide.TAKER,
                    report_id=self._uuid4(),
                    ts_event=now,
                    ts_init=now,
                )
            )
        return reports

    @staticmethod
    def _default_tif() -> Any:
        from nautilus_trader.model.enums import TimeInForce

        return TimeInForce.GTC

    # ──── commands ────
    async def _submit_order(self, command: Any) -> None:
        order = command.order
        instrument = self._instrument_provider.find(order.instrument_id)
        body = {
            "symbol": order.instrument_id.symbol.value,
            "order_type": "BUY" if order.side == OrderSide.BUY else "SELL",
            "volume": float(order.quantity),
            "deviation": self._cfg.deviation,
            "magic": self._cfg.magic,
            "comment": str(order.client_order_id)[:31],
            "type_filling": self._cfg.filling,
        }
        if order.has_price:
            body["price"] = float(order.price)
        self.generate_order_submitted(
            order.strategy_id,
            order.instrument_id,
            order.client_order_id,
            self._clock.timestamp_ns(),
        )
        raw = await self._bridge.order(body)
        result = raw.get("result") or {}
        trade = raw.get("trade") or {}
        retcode = int(result.get("retcode") or 0)
        # The bridge raises on a bad retcode, so a 2xx already means filled -
        # but reading the code when it is there means a future build that stops
        # raising cannot silently look like a fill.
        if not raw.get("success") or retcode not in (0, 10009, 10008):
            self.generate_order_rejected(
                order.strategy_id,
                order.instrument_id,
                order.client_order_id,
                str(result.get("comment") or f"retcode {retcode}"),
                self._clock.timestamp_ns(),
            )
            return
        ticket = result.get("order") or trade.get("transaction_broker_id") or 0
        filled = float(result.get("volume") or trade.get("order_volume") or order.quantity)
        price = float(result.get("price") or trade.get("entry_price") or 0.0)
        now = self._clock.timestamp_ns()
        self.generate_order_accepted(
            order.strategy_id,
            order.instrument_id,
            order.client_order_id,
            VenueOrderId(str(ticket)),
            now,
        )
        self.generate_order_filled(
            order.strategy_id,
            order.instrument_id,
            order.client_order_id,
            VenueOrderId(str(ticket)),
            PositionId(str(ticket)),
            order.side,
            order.order_type,
            Quantity(filled, instrument.size_precision),
            Price(price, instrument.price_precision),
            instrument.quote_currency,
            Money(0.0, instrument.quote_currency),
            LiquiditySide.TAKER,
            now,
        )

    async def _modify_order(self, command: Any) -> None:
        ticket = int(str(command.venue_order_id)) if command.venue_order_id else 0
        if not ticket:
            self._log.error("cannot modify an order with no venue id")
            return
        await self._bridge.modify(
            ticket,
            float(command.trigger_price or 0.0),
            float(command.price or 0.0),
        )

    async def _cancel_order(self, command: Any) -> None:
        ticket = int(str(command.venue_order_id)) if command.venue_order_id else 0
        if ticket:
            await self._bridge.cancel(ticket)

    async def _cancel_all_orders(self, command: Any) -> None:
        for row in await self._bridge.pending():
            if int(row.get("magic") or 0) == self._cfg.magic:
                await self._bridge.cancel(int(row.get("ticket") or 0))

    async def _query_order(self, command: Any) -> None:
        for report in await self._generate_order_status_reports():
            if report.venue_order_id == command.venue_order_id:
                self._send_order_status_report(report)
                return


__all__ = ["ClientOrderId", "MT5ExecutionClient"]
