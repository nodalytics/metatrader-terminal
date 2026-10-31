"""Adapter configuration.

Separate data and execution configs, as the adapter guide asks: a node that
only reads the feed should not have to name credentials it will not use.
"""

from __future__ import annotations

from nautilus_trader.live.config import LiveDataClientConfig, LiveExecClientConfig

#: The bridge's prefix. Every route below hangs off it.
API = "/api/v1"


class MT5BridgeConfig:
    """What both clients need to reach the bridge.

    Not a dataclass mixin - `LiveDataClientConfig` is a frozen pydantic model
    and multiple inheritance across the two would fight its validation - so the
    fields are repeated below and this holds the documentation.
    """


class MT5DataClientConfig(LiveDataClientConfig, frozen=True):
    """Reading the feed.

    `base_url` points at the bridge, not at MT5: the terminal is a Windows
    program and the bridge is the only thing that can safely face a network.
    """

    base_url: str = "http://localhost:8000"
    api_key: str | None = None
    #: Seconds between polls of `symbol_info_tick`.
    #:
    #: **The stream is sampled and the backfill is not.** The bridge's
    #: websocket polls the terminal rather than being pushed to, so the quote
    #: stream is a sample at this interval while `copy_ticks_range` returns
    #: every tick the terminal recorded. A tighter interval costs the terminal
    #: and still samples; history is where completeness lives.
    poll_seconds: float = 0.25
    #: Price decimals per symbol, by Nautilus symbol. See the note in `data.py`:
    #: `Price` is fixed-precision, so this truncates the feed.
    price_precision: dict[str, int] | None = None
    volume_precision: int = 2
    request_timeout: float = 30.0


class MT5ExecClientConfig(LiveExecClientConfig, frozen=True):
    """Placing orders.

    `magic` stamps every order so the desk can tell its own fills from
    anything else on the account - including a human's. `DEFAULT_MAGIC` is
    777_701 and being off by one relabels every strategy as its neighbour.
    """

    base_url: str = "http://localhost:8000"
    api_key: str | None = None
    magic: int = 777_701
    #: Max slippage in points for a market order.
    deviation: int = 20
    #: MT5 order filling mode. The bridge passes it through as `type_filling`.
    filling: str = "IOC"
    request_timeout: float = 30.0
