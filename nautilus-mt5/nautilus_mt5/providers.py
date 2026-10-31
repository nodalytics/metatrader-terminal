"""Instruments, from what the terminal lists.

`InstrumentProvider` is Nautilus's local store of instrument definitions. The
bridge's `/symbols/info/{symbol}` carries everything needed to build one:
digits, point, volume step and limits, contract size.

**Precision comes from the terminal, not from a guess.** `digits` is what the
broker quotes to, and `Price` is fixed-precision, so declaring it wrong
truncates the feed. Measured on the dispersion fold: btc at 2 decimals moves a
1m reading by 3e-5 relative while 15m stays at float noise, because truncation
matters in proportion to how small a bar's range is against its tick size.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AssetClass
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Currency, Price, Quantity

from .bridge import Bridge


class MT5InstrumentProvider(InstrumentProvider):
    """Loads instruments from the bridge.

    Everything is modelled as a `CurrencyPair`, including the synthetics and
    the index CFDs. That is a deliberate simplification rather than an
    oversight: MT5 reports all of them the same way - digits, point, volume
    step, contract size - and Nautilus needs an instrument to price and size
    against, not a taxonomy. `Deriv synthetics are real markets`: a known
    generator and no auction, which changes how they are *researched*, not how
    an order is sized.
    """

    def __init__(self, bridge: Bridge, venue: Venue, symbols: tuple[str, ...] = ()) -> None:
        super().__init__()
        self._bridge = bridge
        self._venue = venue
        self._wanted = symbols

    async def load_all_async(self, filters: dict | None = None) -> None:
        names = list(self._wanted) or await self._bridge.symbols()
        for name in names:
            await self.load_async(InstrumentId(Symbol(name), self._venue))

    async def load_ids_async(
        self, instrument_ids: list[InstrumentId], filters: dict | None = None
    ) -> None:
        for instrument_id in instrument_ids:
            await self.load_async(instrument_id)

    async def load_async(self, instrument_id: InstrumentId, filters: dict | None = None) -> None:
        info = await self._bridge.symbol_info(instrument_id.symbol.value)
        if not info:
            return
        instrument = self._build(instrument_id, info)
        if instrument is not None:
            self.add(instrument)

    def _build(self, instrument_id: InstrumentId, info: dict[str, Any]) -> CurrencyPair | None:
        digits = int(info.get("digits") or 5)
        step = float(info.get("volume_step") or 0.01)
        size_precision = max(0, len(f"{step:.10f}".rstrip("0").split(".")[-1]))
        quote = _currency(info.get("currency_profit") or info.get("currency_margin") or "USD")
        base = _currency(info.get("currency_base") or "USD")
        return CurrencyPair(
            instrument_id=instrument_id,
            raw_symbol=Symbol(instrument_id.symbol.value),
            base_currency=base,
            quote_currency=quote,
            price_precision=digits,
            size_precision=size_precision,
            price_increment=Price(10.0**-digits, digits),
            size_increment=Quantity(step, size_precision),
            lot_size=None,
            max_quantity=_quantity(info.get("volume_max"), size_precision),
            min_quantity=_quantity(info.get("volume_min"), size_precision),
            max_notional=None,
            min_notional=None,
            max_price=None,
            min_price=None,
            # **Measured, not assumed.** The taker fee on this venue is read
            # off the spread rather than a schedule, and `spread-column-is-in-
            # points` is why: the terminal reports spread in points, not
            # price, and reading it as price made EURUSD look like a 476%
            # round trip. Commission is left at zero because this broker
            # charges in the spread.
            margin_init=Decimal("0"),
            margin_maint=Decimal("0"),
            maker_fee=Decimal("0"),
            taker_fee=Decimal("0"),
            ts_event=0,
            ts_init=0,
            info=dict(info),
        )


def _currency(code: str) -> Currency:
    try:
        return Currency.from_str(str(code).upper())
    except Exception:
        return USD


def _quantity(value: Any, precision: int) -> Quantity | None:
    try:
        return Quantity(float(value), precision) if value else None
    except Exception:
        return None


__all__ = ["AssetClass", "MT5InstrumentProvider"]
