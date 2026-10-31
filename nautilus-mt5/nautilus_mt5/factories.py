"""How a node builds these clients.

A factory's `create` is synchronous and returns the client; the client's own
`_connect` does the I/O. That split is the reason `Bridge.open()` exists
separately from its constructor - the event loop is not running yet when this
is called.
"""

from __future__ import annotations

import asyncio
from typing import Any

from nautilus_trader.live.factories import LiveDataClientFactory, LiveExecClientFactory
from nautilus_trader.model.identifiers import ClientId, Venue

from .bridge import Bridge
from .config import MT5DataClientConfig, MT5ExecClientConfig
from .data import MT5DataClient
from .execution import MT5ExecutionClient
from .providers import MT5InstrumentProvider

#: The venue name. One venue, because one terminal is one account - and
#: `polymarket-runs-two-exchanges` is the warning about assuming otherwise:
#: there, a readable balance proved nothing about whether an order would be
#: accepted, because the account belonged to a different exchange than the
#: signature addressed.
MT5_VENUE = "MT5"


class MT5LiveDataClientFactory(LiveDataClientFactory):
    """Builds the read side."""

    @staticmethod
    def create(
        loop: asyncio.AbstractEventLoop,
        name: str,
        config: MT5DataClientConfig,
        msgbus: Any,
        cache: Any,
        clock: Any,
    ) -> MT5DataClient:
        venue = Venue(name or MT5_VENUE)
        bridge = Bridge(config.base_url, config.api_key, timeout=config.request_timeout)
        return MT5DataClient(
            loop=loop,
            client_id=ClientId(name or MT5_VENUE),
            venue=venue,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=MT5InstrumentProvider(bridge, venue),
            config=config,
        )


class MT5LiveExecClientFactory(LiveExecClientFactory):
    """Builds the order side."""

    @staticmethod
    def create(
        loop: asyncio.AbstractEventLoop,
        name: str,
        config: MT5ExecClientConfig,
        msgbus: Any,
        cache: Any,
        clock: Any,
    ) -> MT5ExecutionClient:
        venue = Venue(name or MT5_VENUE)
        bridge = Bridge(config.base_url, config.api_key, timeout=config.request_timeout)
        return MT5ExecutionClient(
            loop=loop,
            client_id=ClientId(name or MT5_VENUE),
            venue=venue,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=MT5InstrumentProvider(bridge, venue),
            config=config,
        )
