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

import hashlib
import hmac
import logging
import os
import re
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


#: Every `MetaTrader5` function the API calls, and nothing else.
#:
#: Derived from the application's own source and **pinned by a test** that
#: re-derives it and fails on any difference, so a new call site cannot
#: silently find itself refused over rpyc while working in-process.
#:
#: The list matters because the package is not only a data interface.
#: `initialize(path)` *launches* the executable it is handed, so "call any
#: public function on the module" is a code-execution primitive and not merely
#: "can place trades".
CALLS = frozenset(
    {
        "account_info",
        "copy_rates_from",
        "copy_rates_from_pos",
        "copy_rates_range",
        "copy_ticks_from",
        "copy_ticks_range",
        "history_deals_get",
        "history_orders_get",
        "initialize",
        "last_error",
        "login",
        "market_book_add",
        "market_book_get",
        "market_book_release",
        "order_calc_margin",
        "order_calc_profit",
        # Read-only despite the name and the neighbours: `order_check`
        # validates a request and returns the margin it would need, without
        # sending anything. `POST /trading/order_check` is the only caller.
        "order_check",
        "order_send",
        "orders_get",
        "orders_total",
        "positions_get",
        "shutdown",
        "symbol_info",
        "symbol_info_tick",
        "symbol_select",
        "symbols_get",
        "terminal_info",
        "version",
    }
)

#: Calls whose arguments come from **this** host and never from the client.
#:
#: `initialize` takes the path of a terminal to launch and the credentials to
#: log in with. Both describe this machine, which the client has no knowledge
#: of and no business naming: a client-supplied path is an arbitrary
#: executable, and client-supplied credentials point the terminal at an
#: account of the caller's choosing. The client's *intent* is still honoured -
#: whether to send credentials at all, which `connector.py` depends on because
#: re-sending them raises an "account changed" event that disables algo
#: trading - but every value is this host's.
PINNED = frozenset({"initialize", "login"})

#: A constant's name. MT5's are all upper case; the pattern is what stops
#: `const` from being a general attribute reader.
CONSTANT = re.compile(r"^[A-Z][A-Z0-9_]*$")

#: The terminal's own path, the same default `connector.py` uses in-process.
TERMINAL_PATH = os.environ.get("MT5_TERMINAL_PATH") or "C:\\Metatrader-5\\terminal64.exe"


def _terminal() -> Any:
    import MetaTrader5 as mt5  # noqa: N813 - the package's own name

    return mt5


def _credentials() -> dict[str, Any]:
    """Login, password and server, read here rather than accepted there."""
    return {
        "login": int(os.environ.get("MT5_LOGIN") or 0),
        "password": os.environ.get("MT5_PASSWORD") or "",
        "server": os.environ.get("MT5_SERVER") or "",
    }


def _function(name: str) -> Any:
    """One allowed function, or `AttributeError` naming it."""
    if name not in CALLS:
        raise AttributeError(
            f"{name!r} is not a function this service offers. The list is "
            f"what the API calls and is deliberately closed: 'any public "
            f"function on the module' includes initialize(), which launches "
            f"the executable it is given."
        )
    found = getattr(_terminal(), name)
    if not callable(found):
        raise TypeError(f"{name!r} is a constant, not a function; ask for it with const()")
    return found


def _constant(name: str) -> Any:
    """One constant, or `AttributeError`.

    The pattern is the whole access policy here: without it `const` would
    serve `__loader__`, `__builtins__` and anything reachable from them, which
    is the remote-interpreter surface a three-method service exists to avoid.
    """
    if not CONSTANT.match(name or ""):
        raise AttributeError(f"{name!r} is not a constant of the terminal's interface")
    found = getattr(_terminal(), name)
    if callable(found):
        raise TypeError(f"{name!r} is a function, not a constant; call it with call()")
    return found


def _local_arguments(name: str, keyword: dict[str, Any]) -> tuple[tuple, dict[str, Any]]:
    """What `initialize`/`login` are really called with, built here.

    The client's values are dropped and its *intent* kept: `connector.py`
    calls `initialize(path, portable=True)` first and only retries with
    credentials if that fails, because re-sending them raises an "account
    changed" event that switches algo trading off. So "did the client ask for
    credentials" is honoured; which credentials is not its decision.
    """
    wants_credentials = any(key in keyword for key in ("login", "password", "server"))
    local: dict[str, Any] = {"portable": bool(keyword.get("portable", True))}
    if wants_credentials:
        local.update(_credentials())
    if name == "login":
        credentials = _credentials()
        return (credentials.pop("login"),), credentials
    return (TERMINAL_PATH,), local


class TerminalService:
    """`MetaTrader5` over rpyc, as values.

    Subclassing `rpyc.Service` is deliberately avoided at import time so this
    module can be read and unit-tested without rpyc installed; `serve()` mixes
    it in. rpyc only requires the `exposed_` prefix.
    """

    def exposed_callables(self) -> tuple[str, ...]:
        """The functions this service offers, asked once at connect.

        The client needs to know whether a name is a function or a constant
        *before* it decides what to do with it, and the constants
        (`TIMEFRAME_M15`, `ORDER_TYPE_BUY`, `TRADE_RETCODE_DONE`) outnumber the
        functions. One round trip here spares a round trip per attribute.
        """
        return tuple(sorted(CALLS))

    def exposed_call(self, name: str, args: tuple, kwargs: tuple) -> tuple:
        """Call one allowed function, with wire-encoded arguments and result."""
        found = _function(name)
        positional = tuple(decode(item) for item in args)
        keyword = {str(key): decode(item) for key, item in kwargs}
        if name in PINNED:
            positional, keyword = _local_arguments(name, keyword)
        return encode(found(*positional, **keyword))

    def exposed_const(self, name: str) -> tuple:
        """One constant, wire-encoded."""
        return encode(_constant(name))


#: The handshake is one sha256 digest, so the secret itself never crosses.
TOKEN_BYTES = 32


def token(secret: str) -> bytes:
    """What a client sends and a server expects. Shared by both ends."""
    return hashlib.sha256(secret.encode("utf-8")).digest()


def _authenticator(expected: bytes) -> Any:
    """rpyc's pre-protocol hook, checking a shared secret.

    Loopback is not an identity: every process on this host can reach the
    port, and the one that reaches it can place trades. The digest is compared
    with `hmac.compare_digest` so a wrong secret cannot be found a byte at a
    time.

    It crosses in clear, which is why it is not a substitute for the loopback
    guard - it answers "which local process", not "which machine". On a
    network, use the SSH tunnel or `rpyc.ssl_connect`.
    """
    from rpyc.utils.authenticators import AuthenticationError

    def check(sock: Any) -> tuple:
        sock.settimeout(10.0)
        got = b""
        try:
            while len(got) < TOKEN_BYTES:
                chunk = sock.recv(TOKEN_BYTES - len(got))
                if not chunk:
                    raise AuthenticationError("the client closed before sending a secret")
                got += chunk
        except OSError as exc:
            raise AuthenticationError(f"no secret arrived: {exc}") from exc
        sock.settimeout(None)
        if not hmac.compare_digest(got, expected):
            raise AuthenticationError("the shared secret does not match")
        return sock, None

    return check


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
    secret = os.environ.get("MT5_RPYC_SECRET") or ""
    if host not in LOOPBACK and not secret:
        raise ValueError(
            "MT5_RPYC_ALLOW_REMOTE is set but MT5_RPYC_SECRET is not. A port "
            "reachable from the network with no handshake at all is one "
            "anybody can trade through."
        )
    service = type("TerminalService", (TerminalService, rpyc.Service), {})
    return ThreadedServer(
        service,
        hostname=host,
        port=port,
        protocol_config=POLICY,
        authenticator=_authenticator(token(secret)) if secret else None,
    )


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
