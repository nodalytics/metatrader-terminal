"""The terminal side of the rpyc option: three calls, and no attribute access.

Run this **beside the terminal** - inside the Wine container, where the
win32-only `MetaTrader5` package can import - when an API running elsewhere
should drive it:

    MT5_RPYC_PORT=18812 python -m app.services.terminal_server

The API then runs anywhere, with `MT5_TRANSPORT=rpyc`. See
`terminal_transport.py` for what that buys and what it does not (it is not
faster; an in-process call has no hop to remove).

## Why a service with three methods rather than `rpyc.classic`

A classic rpyc server is a remote Python interpreter: whoever reaches the port
can import, read and execute anything the process can. This service exposes
`callables`, `call` and `const`, and rpyc's default policy refuses everything
else, so the reachable surface is "call a function on the `MetaTrader5` module"
and not "run code on the terminal host".

Arguments and results cross as the tagged primitive trees of
`terminal_wire.py`, so **no proxy object crosses in either direction**. That is
what lets both ends keep `allow_all_attrs=False`: nothing on either side ever
needs to read an attribute of an object belonging to the other.

## What this still is

A caller who reaches this port can place trades. There is no authentication in
the protocol and the channel is plaintext, which is why both ends refuse a
non-loopback address unless told the network was secured another way - reach it
through an **SSH tunnel**, as the rest of this stack reaches a remote terminal,
or use `rpyc.ssl_connect` with mutual TLS. Binding this to a public interface
is equivalent to publishing the trading password.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .terminal_wire import decode, encode, flag

log = logging.getLogger(__name__)

#: Addresses that need no further argument, matching the client's own list.
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})

#: rpyc's defaults, written out. Being explicit is the point: these are the
#: restrictions the wire format exists to make affordable, and a future rpyc
#: release changing a default should not quietly widen them.
POLICY = {
    "allow_all_attrs": False,
    "allow_public_attrs": False,
    "allow_setattr": False,
    "allow_delattr": False,
    "allow_pickle": False,
}


def _terminal() -> Any:
    import MetaTrader5 as mt5  # noqa: N813 - the package's own name

    return mt5


def _named(name: str) -> Any:
    """One attribute of the package, or `AttributeError` saying so.

    The underscore check is the whole access policy: without it `const` would
    serve `__loader__`, `__builtins__` and anything reachable from them, which
    would hand back the remote-interpreter surface this service exists to
    avoid.
    """
    if not name or name.startswith("_"):
        raise AttributeError(f"{name!r} is not part of the terminal's public interface")
    return getattr(_terminal(), name)


class TerminalService:
    """`MetaTrader5` over rpyc, as values.

    Subclassing `rpyc.Service` is deliberately avoided at import time so this
    module can be read and unit-tested without rpyc installed; `serve()` mixes
    it in. rpyc only requires the `exposed_` prefix.
    """

    def exposed_callables(self) -> tuple[str, ...]:
        """Every function the package offers, asked once at connect.

        The client needs to know whether a name is a function or a constant
        *before* it decides what to do with it, and the constants
        (`TIMEFRAME_M15`, `ORDER_TYPE_BUY`, `TRADE_RETCODE_DONE`) outnumber the
        functions. One round trip here spares a round trip per attribute.
        """
        mt5 = _terminal()
        return tuple(
            sorted(
                name
                for name in dir(mt5)
                if not name.startswith("_") and callable(getattr(mt5, name, None))
            )
        )

    def exposed_call(self, name: str, args: tuple, kwargs: tuple) -> tuple:
        """Call one function, with wire-encoded arguments and result."""
        found = _named(name)
        if not callable(found):
            raise TypeError(f"{name!r} is a constant, not a function; ask for it with const()")
        positional = tuple(decode(item) for item in args)
        keyword = {str(key): decode(item) for key, item in kwargs}
        return encode(found(*positional, **keyword))

    def exposed_const(self, name: str) -> tuple:
        """One constant, wire-encoded."""
        found = _named(name)
        if callable(found):
            raise TypeError(f"{name!r} is a function, not a constant; call it with call()")
        return encode(found)


def listener(host: str, port: int) -> Any:
    """A bound, not-yet-serving `ThreadedServer`, or `ValueError` saying why not.

    Separate from `serve()` so a test can take one on an ephemeral port and
    drive the real policy, the real service and the real guard rather than a
    rehearsal of them. rpyc binds in the constructor, so `.port` is the chosen
    port and a client may connect before the accept loop runs.

    Refuses a non-loopback bind without `MT5_RPYC_ALLOW_REMOTE=1` for the same
    reason the client refuses a non-loopback host: the channel is plaintext and
    unauthenticated, and it carries trade instructions.
    """
    import rpyc
    from rpyc.utils.server import ThreadedServer

    if host not in LOOPBACK and not flag("MT5_RPYC_ALLOW_REMOTE"):
        raise ValueError(
            f"MT5_RPYC_BIND={host!r} is not loopback. Anyone who reaches this "
            f"port can place trades over a plaintext, unauthenticated channel. "
            f"Bind loopback and reach it through an SSH tunnel, or set "
            f"MT5_RPYC_ALLOW_REMOTE=1 to say you have secured it another way."
        )
    service = type("TerminalService", (TerminalService, rpyc.Service), {})
    return ThreadedServer(service, hostname=host, port=port, protocol_config=POLICY)


def serve() -> None:
    """Listen, until killed."""
    host = (os.environ.get("MT5_RPYC_BIND") or "127.0.0.1").strip()
    port = int(os.environ.get("MT5_RPYC_PORT") or 18812)
    server = listener(host, port)
    log.info("terminal rpyc service listening on %s:%d", host, server.port)
    server.start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    serve()
