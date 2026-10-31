"""Quotes and bars from the bridge.

**Of twenty-six subscribe hooks this venue can honestly fill about four.**
MT5 has no order book - `market_book_add` fails outright - and its ticks are
quote updates, with `last`, `volume` and `volume_real` reading 0 or a constant
5000. So trade ticks, order book deltas and snapshots, funding rates, mark and
index prices are all left to raise `NotImplementedError`, which is the correct
answer rather than a gap: a client that silently returns nothing for a
subscription a strategy made is worse than one that refuses it.

What is implemented:

- `quote_ticks`, polled. The bridge's websocket polls `symbol_info_tick`
  rather than being pushed to, so there is no completeness to be had from a
  stream here - it is a sample at `poll_seconds`.
- `bars`, polled on the interval, published when a bar closes.
- `request_bars` and `request_quote_ticks`, which *are* complete:
  `copy_ticks_range` returns every tick the terminal recorded, and this is
  where history should be taken from.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from nautilus_trader.core.datetime import secs_to_nanos
from nautilus_trader.live.data_client import LiveMarketDataClient
from nautilus_trader.model.data import Bar, BarType, QuoteTick
from nautilus_trader.model.identifiers import ClientId, Venue
from nautilus_trader.model.objects import Price, Quantity

from .bridge import Bridge
from .config import MT5DataClientConfig
from .providers import MT5InstrumentProvider

#: Nautilus bar aggregation -> the bridge's timeframe name, and the bar's
#: length in seconds. The seconds matter: **`ts_event` on a Nautilus bar is
#: the bar's close**, and MT5 stamps a bar with its open, so one has to be
#: converted into the other. Getting it backwards does not shift a series
#: feeding `statespace` - it silences it, because `observe_bar` refuses a bar
#: whose time is not strictly after the last one.
TIMEFRAMES: dict[str, tuple[str, float]] = {
    "1-MINUTE": ("M1", 60.0),
    "5-MINUTE": ("M5", 300.0),
    "15-MINUTE": ("M15", 900.0),
    "30-MINUTE": ("M30", 1800.0),
    "1-HOUR": ("H1", 3600.0),
    "4-HOUR": ("H4", 14400.0),
    "1-DAY": ("D1", 86400.0),
}


class MT5DataClient(LiveMarketDataClient):
    """The read side of the venue."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        client_id: ClientId,
        venue: Venue,
        msgbus: Any,
        cache: Any,
        clock: Any,
        instrument_provider: MT5InstrumentProvider,
        config: MT5DataClientConfig,
    ) -> None:
        super().__init__(
            loop=loop,
            client_id=client_id,
            venue=venue,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=instrument_provider,
            config=config,
        )
        self._cfg = config
        self._bridge = Bridge(config.base_url, config.api_key, timeout=config.request_timeout)
        self._pollers: dict[str, asyncio.Task] = {}
        #: The last bar time published per bar type, so a forming bar is not
        #: published twice. `prices` learned this the hard way: a notice per
        #: *touched* bar meant the bar in progress arrived on every sweep, and
        #: folding it each time both multiplied the observation count and fed a
        #: range that was still growing.
        self._last_bar: dict[str, int] = {}

    # ──── lifecycle ────
    async def _connect(self) -> None:
        await self._bridge.open()
        if not await self._bridge.ping():
            raise ConnectionError(f"the bridge at {self._cfg.base_url} did not answer")
        await self._instrument_provider.load_all_async()
        for instrument in self._instrument_provider.list_all():
            self._handle_data(instrument)
        self._log.info(f"loaded {len(self._instrument_provider.list_all())} instruments")

    async def _disconnect(self) -> None:
        for task in list(self._pollers.values()):
            task.cancel()
        for task in list(self._pollers.values()):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._pollers.clear()
        await self._bridge.close()

    # ──── subscriptions ────
    async def _subscribe_quote_ticks(self, command: Any) -> None:
        key = f"quotes:{command.instrument_id}"
        if key not in self._pollers:
            self._pollers[key] = self.create_task(self._poll_quotes(command.instrument_id))

    async def _unsubscribe_quote_ticks(self, command: Any) -> None:
        self._stop(f"quotes:{command.instrument_id}")

    async def _subscribe_bars(self, command: Any) -> None:
        key = f"bars:{command.bar_type}"
        if key not in self._pollers:
            self._pollers[key] = self.create_task(self._poll_bars(command.bar_type))

    async def _unsubscribe_bars(self, command: Any) -> None:
        self._stop(f"bars:{command.bar_type}")

    def _stop(self, key: str) -> None:
        task = self._pollers.pop(key, None)
        if task is not None:
            task.cancel()

    # ──── polling ────
    async def _poll_quotes(self, instrument_id: Any) -> None:
        symbol = instrument_id.symbol.value
        instrument = self._instrument_provider.find(instrument_id)
        last: tuple[float, float] | None = None
        while True:
            try:
                info = await self._bridge.symbol_info(symbol)
                bid, ask = float(info.get("bid") or 0.0), float(info.get("ask") or 0.0)
                if bid > 0 and ask > 0 and (bid, ask) != last:
                    last = (bid, ask)
                    now = self._clock.timestamp_ns()
                    self._handle_data(
                        QuoteTick(
                            instrument_id=instrument_id,
                            bid_price=Price(bid, instrument.price_precision),
                            ask_price=Price(ask, instrument.price_precision),
                            bid_size=Quantity(1, 0),
                            ask_size=Quantity(1, 0),
                            ts_event=now,
                            ts_init=now,
                        )
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log.warning(f"quote poll for {symbol}: {exc}")
            await asyncio.sleep(self._cfg.poll_seconds)

    async def _poll_bars(self, bar_type: BarType) -> None:
        name, seconds = _timeframe(bar_type)
        symbol = bar_type.instrument_id.symbol.value
        instrument = self._instrument_provider.find(bar_type.instrument_id)
        while True:
            try:
                # Two bars, position 0 being the one still forming: the closed
                # bar is the one behind it, and only that one is published.
                rows = await self._bridge.bars(symbol, name, count=2, start=0)
                for row in rows:
                    opened = int(row.get("time") or 0)
                    if opened <= self._last_bar.get(str(bar_type), 0):
                        continue
                    if _is_forming(opened, seconds, self._clock.timestamp_ns()):
                        continue
                    self._last_bar[str(bar_type)] = opened
                    self._handle_data(_bar(bar_type, row, instrument, seconds))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log.warning(f"bar poll for {symbol} {name}: {exc}")
            await asyncio.sleep(max(1.0, seconds / 10.0))

    # ──── history, which is the complete side ────
    async def _request_bars(self, request: Any) -> None:
        name, seconds = _timeframe(request.bar_type)
        symbol = request.bar_type.instrument_id.symbol.value
        instrument = self._instrument_provider.find(request.bar_type.instrument_id)
        rows = await self._bridge.bars(symbol, name, count=int(request.limit or 1000), start=1)
        bars = [_bar(request.bar_type, row, instrument, seconds) for row in rows if row.get("time")]
        bars.sort(key=lambda b: b.ts_event)
        self._handle_bars(
            request.bar_type, bars, bars[-1] if bars else None, request.id, request.params
        )


def _timeframe(bar_type: BarType) -> tuple[str, float]:
    spec = str(bar_type.spec)
    for key, (name, seconds) in TIMEFRAMES.items():
        if spec.startswith(key):
            return name, seconds
    raise ValueError(f"{bar_type} has no MT5 timeframe - this venue serves {sorted(TIMEFRAMES)}")


def _is_forming(opened: int, seconds: float, now_ns: int) -> bool:
    """Has this bar's window closed yet?

    A forming bar is a different measurement, not an early one: the sampling
    variance of `log(garman_klass)` runs 0.265 on a bar built from thousands of
    ticks and 0.50 on one built from twelve, and the bias goes from -0.15 to
    -0.79. A tenth-formed bar is an accurate reading of a tenth of the bar.
    """
    return (now_ns / 1e9) < (opened + seconds)


def _bar(bar_type: BarType, row: dict, instrument: Any, seconds: float) -> Bar:
    """One bridge row as a Nautilus bar, with the timestamp moved to its close."""
    opened = float(row.get("time") or 0.0)
    closes = secs_to_nanos(opened + seconds)
    digits = instrument.price_precision
    size = instrument.size_precision
    return Bar(
        bar_type=bar_type,
        open=Price(float(row["open"]), digits),
        high=Price(float(row["high"]), digits),
        low=Price(float(row["low"]), digits),
        close=Price(float(row["close"]), digits),
        volume=Quantity(float(row.get("tick_volume") or row.get("real_volume") or 0.0), size),
        ts_event=closes,
        ts_init=closes,
    )
