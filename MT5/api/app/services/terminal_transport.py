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

## The rpyc channel is loopback-only, and carries no pickle

Two deliberate restrictions, because an rpyc connection is a remote-code
channel by default and this one would be carrying trade instructions.

**No `classic.obtain`.** That function pickles on the far side, so a
compromised or impersonated terminal server could execute code in this process
simply by returning a value. Results are converted field-wise instead: a
namedtuple's `_fields` and `tuple(result)` are both tuples of primitives, which
rpyc passes **by value** through brine with no pickling anywhere.

**Loopback unless explicitly overridden**, because the channel is plaintext and
unauthenticated. A non-local host needs `MT5_RPYC_ALLOW_REMOTE=1` *and* should
be reached the way everything else in this stack reaches a remote terminal - an
SSH tunnel - rather than by opening the port. `rpyc.ssl_connect` with mutual
TLS is the alternative if a tunnel is not available.

`allow_all_attrs` stays and is the one concession: the conversion needs
`_fields` and `_asdict`, which rpyc's default policy blocks as private. The
narrower fix is an `exposed_` service on the terminal side returning plain
dicts, which removes the need for attribute access at all; that is the right
shape if this option is ever pointed at a host someone else controls.

## Why the calls are materialised here rather than at the call sites

**rpyc returns references, not values.** A namedtuple like `symbol_info()` or
`order_send()`'s result comes back as a netref: attribute access works, and
`FastAPI` then tries to serialise a proxy object. Primitives and tuples of
primitives cross by value, which is the trap - the common cases look fine and
the structured ones do not.

There are around a hundred and fifteen `mt5.` call sites across twelve modules,
eighty-four of them in `trade.py`. Materialising at each one means a hundred and
fifteen places that can forget, and forgetting is silent until something
serialises oddly. Doing it in the proxy means a new call site **cannot** forget,
which is the only version worth shipping in a service whose recurring failure
is the silent omission.

So `_Remote.__getattr__` returns a wrapper for a callable and an obtained value
for anything else, and the constants - `TIMEFRAME_M15`, `ORDER_TYPE_BUY`,
`TRADE_RETCODE_DONE`, which outnumber the calls - come across as plain ints.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

#: Transports this module knows. `inprocess` imports the package; `rpyc`
#: proxies a terminal elsewhere.
TRANSPORTS = ("inprocess", "rpyc")


def _flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() not in ("", "0", "false", "no", "off")


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
    """`mt5` over rpyc, with every result materialised.

    Attribute access is proxied so a call site reads exactly as it does against
    the package. A callable is wrapped; anything else - a constant - is
    obtained immediately, because `TIMEFRAME_M15` must be an `int` by the time
    anyone compares it.
    """

    #: Hosts that need no further argument. Anything else is a plaintext,
    #: unauthenticated connection across a network and has to be asked for.
    LOCAL = frozenset({"127.0.0.1", "::1", "localhost"})

    def __init__(self, host: str, port: int, timeout: float) -> None:
        import rpyc

        if host not in self.LOCAL and not _flag("MT5_RPYC_ALLOW_REMOTE"):
            raise ValueError(
                f"MT5_RPYC_HOST={host!r} is not loopback. This channel is "
                f"plaintext and unauthenticated, and it carries trade "
                f"instructions - reach a remote terminal through an SSH tunnel "
                f"to 127.0.0.1, which is how the rest of this stack does it, or "
                f"set MT5_RPYC_ALLOW_REMOTE=1 to say you have secured it another "
                f"way."
            )
        # `allow_all_attrs` is needed for `_fields`/`_asdict`, which the
        # conversion below reads and rpyc's default policy blocks as private.
        # See the note above on the `exposed_` alternative.
        self._conn = rpyc.connect(
            host,
            port,
            config={"allow_all_attrs": True, "sync_request_timeout": timeout},
        )
        self._mt5 = self._conn.root.mt5
        log.info("mt5 transport: rpyc to %s:%d", host, port)

    def _obtain(self, value: Any) -> Any:
        """A value rather than a reference, **without pickling anything.**

        `rpyc.utils.classic.obtain` would do this in one line and unpickles on
        this side, which makes any value the far side returns a code path into
        this process. So the conversion is explicit: a namedtuple becomes a
        plain dict built from `_fields` and `tuple(value)`, both of which are
        tuples of primitives and cross by value through brine.

        A shape this does not recognise is returned as the reference it is.
        Attribute access on a netref still works, so a route reading one field
        keeps working, and the alternative - raising - would fail a request over
        a value nobody looked at.
        """
        if value is None or isinstance(value, (bool, int, float, str, bytes)):
            return value
        fields = getattr(value, "_fields", None)
        if fields is not None:
            # A namedtuple: MT5 returns these for ticks, symbols, positions,
            # orders and order results.
            names = tuple(str(name) for name in fields)
            return dict(zip(names, tuple(value), strict=False))
        if isinstance(value, (tuple, list)):
            return [self._obtain(item) for item in value]
        try:
            # A tuple of namedtuples - `positions_get`, `history_deals_get` -
            # arrives as a netref sequence rather than a `tuple` here.
            return [self._obtain(item) for item in value]
        except TypeError:
            log.warning(
                "rpyc returned a %r this transport cannot convert; using the "
                "reference, whose attribute access still works",
                type(value).__name__,
            )
            return value

    def __getattr__(self, name: str) -> Any:
        found = getattr(self._mt5, name)
        if not callable(found):
            return self._obtain(found)

        def call(*args: Any, **kwargs: Any) -> Any:
            return self._obtain(found(*args, **kwargs))

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
