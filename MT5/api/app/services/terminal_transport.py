"""Where `mt5` comes from: this process, or a remote terminal over rpyc.

**`inprocess` is the default and nothing about it changes.** The `MetaTrader5`
package is win32-only and talks to a terminal on the same machine, which is why
this service has always run inside the terminal's container. That path is
microseconds and is what every existing deployment uses.

## What the rpyc option is for, and what it is not for

It is **not** faster. An in-process call into the terminal has no network hop
to remove, so routing it through rpyc adds one. What it buys is *where the API
may run*: outside the Wine container, on Linux, or one API against several
terminals - which the in-process import makes impossible.

If latency is the goal, the lever is `MT5/ea/TillTickFeed.mq5`: `OnTick` is a
real callback, so an EA pushes **every** tick where a poll is a sample, and a
quote that moved and moved back between two polls never happened as far as a
consumer is concerned.

## Nothing crosses as a reference, in either direction

The terminal side is `terminal_server.py`, a service with three methods, and
the values in both directions are the tagged primitive trees of
`terminal_wire.py`. That is the whole security argument and it is structural
rather than a policy setting: because no proxy object ever crosses, neither end
needs to read an attribute of an object belonging to the other, so **both ends
keep rpyc's restrictive defaults** - `allow_all_attrs` and `allow_pickle` off.

The first version of this transport did neither of those things and the wire
module records what each one cost: a `dict` argument arriving as a netref
forced `allow_all_attrs=True`, and `rpyc.utils.classic.obtain` - which would
convert a result in one line - unpickles on this side, making any value the far
end returns a code path into this process.

**Tightening the policy would not have been enough.** `__class__` and
`__call__` are on rpyc's safe list in every configuration, so any reference
handed over is a foothold regardless: measured under the restrictive defaults,
the far side reached `object.__subclasses__()` and called a constructor that
ran *here*. Sending values instead is the only version of this that holds.

**Loopback unless explicitly overridden**, because the channel is still
plaintext and unauthenticated and a caller who reaches it can trade. A non-local
host needs `MT5_RPYC_ALLOW_REMOTE=1` *and* should be reached the way everything
else in this stack reaches a remote terminal - an SSH tunnel - rather than by
opening the port. `rpyc.ssl_connect` with mutual TLS is the alternative.

## Why the calls are materialised here rather than at the call sites

**rpyc returns references, not values**, for everything but primitives and
tuples of primitives - which is the trap, because the common cases look fine
and the structured ones do not. A `symbol_info()` namedtuple comes back as a
netref, attribute access works, and `FastAPI` then tries to serialise a proxy.

There are around a hundred and fifteen `mt5.` call sites across twelve modules,
eighty-four of them in `trade.py`. Materialising at each one means a hundred and
fifteen places that can forget, and forgetting is silent until something
serialises oddly. Doing it in the proxy means a new call site **cannot** forget,
which is the only version worth shipping in a service whose recurring failure
is the silent omission.

So `_Remote.__getattr__` returns a wrapper for a callable and a decoded value
for anything else, and the constants - `TIMEFRAME_M15`, `ORDER_TYPE_BUY`,
`TRADE_RETCODE_DONE`, which outnumber the calls - arrive as plain ints and are
cached, because a constant does not change.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .terminal_wire import decode, encode, flag

log = logging.getLogger(__name__)

#: Transports this module knows. `inprocess` imports the package; `rpyc`
#: proxies a terminal elsewhere.
TRANSPORTS = ("inprocess", "rpyc")


def _configured() -> str:
    raw = (os.environ.get("MT5_TRANSPORT") or "inprocess").strip().lower()
    if raw not in TRANSPORTS:
        raise ValueError(
            f"MT5_TRANSPORT={raw!r} is not one of {', '.join(TRANSPORTS)}. "
            f"Refusing rather than falling back: a service that quietly used a "
            f"different terminal than it was told to is worse than one that "
            f"will not start."
        )
    return raw


class _Remote:
    """`mt5` over rpyc, with every value decoded.

    Attribute access is proxied so a call site reads exactly as it does against
    the package: a record keeps `_asdict` and `_fields`, a bar table keeps its
    column names, and a constant is an `int` by the time anyone compares it.
    """

    #: Hosts that need no further argument. Anything else is a plaintext,
    #: unauthenticated connection across a network and has to be asked for.
    LOCAL = frozenset({"127.0.0.1", "::1", "localhost"})

    #: Class-level, so an attribute miss during `__init__` cannot recurse
    #: through `__getattr__` into a half-built object.
    _calls: frozenset[str] = frozenset()

    def __init__(self, host: str, port: int, timeout: float) -> None:
        import rpyc

        if host not in self.LOCAL and not flag("MT5_RPYC_ALLOW_REMOTE"):
            raise ValueError(
                f"MT5_RPYC_HOST={host!r} is not loopback. This channel is "
                f"plaintext and unauthenticated, and it carries trade "
                f"instructions - reach a remote terminal through an SSH tunnel "
                f"to 127.0.0.1, which is how the rest of this stack does it, or "
                f"set MT5_RPYC_ALLOW_REMOTE=1 to say you have secured it another "
                f"way."
            )
        # Only the timeout. Every other setting stays at rpyc's default, which
        # is the restrictive one; `terminal_wire` exists so that is affordable.
        self._conn = rpyc.connect(host, port, config={"sync_request_timeout": timeout})
        self._root = self._conn.root
        self._calls = frozenset(self._root.callables())
        self._const: dict[str, Any] = {}
        log.info("mt5 transport: rpyc to %s:%d, %d functions", host, port, len(self._calls))

    def __getattr__(self, name: str) -> Any:
        # A private name is never part of the terminal's interface, and
        # answering one here is how a construction-time miss becomes infinite
        # recursion instead of an `AttributeError`.
        if name.startswith("_"):
            raise AttributeError(name)
        if name not in self._calls:
            if name not in self._const:
                self._const[name] = decode(self._root.const(name))
            return self._const[name]

        def call(*args: Any, **kwargs: Any) -> Any:
            return decode(
                self._root.call(
                    name,
                    tuple(encode(arg) for arg in args),
                    tuple((str(key), encode(value)) for key, value in kwargs.items()),
                )
            )

        call.__name__ = name
        return call


def _build() -> Any:
    kind = _configured()
    if kind == "inprocess":
        import MetaTrader5 as mt5  # noqa: N813 - the package's own name

        return mt5
    return _Remote(
        host=os.environ.get("MT5_RPYC_HOST") or "127.0.0.1",
        port=int(os.environ.get("MT5_RPYC_PORT") or 18812),
        timeout=float(os.environ.get("MT5_RPYC_TIMEOUT_S") or 30.0),
    )


#: The terminal. Built once at import, because `inprocess` is a module import
#: and an rpyc connection is a socket neither of which wants to be per-call.
mt5 = _build()
