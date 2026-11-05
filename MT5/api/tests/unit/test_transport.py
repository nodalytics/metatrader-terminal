"""The rpyc option, over a real connection to a real service.

Every test here exists because the first version of the transport failed it.
It routed values through netrefs - rpyc proxies - which works for attribute
access and breaks everywhere else, and bought the attribute access with
`allow_all_attrs=True`, which is a code path into the API process from a
terminal that has been impersonated.

The connection is real: `terminal_server.listener` on an ephemeral port in a
thread, with the production policy, serving the fake terminal that `conftest`
has already installed as `MetaTrader5`. A test against a stubbed connection
would have passed on the broken version, because what was broken was the
connection's own behaviour.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import pathlib
import threading

import numpy as np
import pytest

from app.services.terminal_server import CALLS
from app.services.terminal_wire import decode, encode

rpyc = pytest.importorskip("rpyc")
brine = pytest.importorskip("rpyc.core.brine")


# ------------------------------------------------------------------ the wire


def test_every_wire_form_is_something_brine_can_send():
    """The constraint the whole module exists for.

    rpyc's `brine` sends primitives and tuples of primitives **by value** and
    everything else - `dict` and `list` included - as a reference. A wire form
    brine refuses would arrive as a proxy, which is the failure this encoding
    was written to remove, so the encoder's output is checked against brine's
    own predicate rather than against a list of types written here.
    """
    rows = np.array([(1, 2.0), (3, 4.0)], dtype=[("time", "<i8"), ("open", "<f8")])
    for value in (
        None,
        1,
        1.5,
        True,
        "x",
        b"y",
        {"action": 1, "nested": {"sl": 0.0}},
        [1, 2, 3],
        (1, "a"),
        dt.datetime(2026, 1, 1, 12, 0),
        np.float64(2.5),
        rows,
        rows[0],
    ):
        assert brine.dumpable(encode(value)), f"{type(value).__name__} would cross as a reference"


def test_a_record_survives_as_a_record():
    """`_asdict()` is called at four routes' worth of call sites.

    `history.py` does `[d._asdict() for d in deals]` and `market_data.py` does
    `info._asdict()`. The first transport decoded a namedtuple into a plain
    dict, which has no `_asdict`, so `/history/deals`, `/history/orders`,
    `/symbols/info/{symbol}` and `/symbols/ticks/{symbol}` would all have
    raised `AttributeError` on their first request.
    """
    from collections import namedtuple

    original = namedtuple("Deal", "ticket profit symbol")(7, -1.25, "XAUUSD")
    landed = decode(encode(original))

    assert landed._asdict() == {"ticket": 7, "profit": -1.25, "symbol": "XAUUSD"}
    assert landed.profit == -1.25
    assert landed[0] == 7
    assert tuple(landed) == (7, -1.25, "XAUUSD")


def test_a_bar_table_keeps_its_column_names():
    """`copy_rates_*` returns a numpy structured array and `market_data.py`
    hands it straight to `pd.DataFrame`, then reads `df['time']`.

    Iterating that array through a netref yields bare rows with the field names
    gone, so the DataFrame's columns became `0..7` and `df['time']` raised
    `KeyError` - a 500 on every bar and tick request. `numpy.void` carries both
    `dtype.names` and `.item()`, and `.item()` is the lossy one, which is why
    the encoder checks the structured shape first.
    """
    rows = np.array(
        [(1_700_000_000, 4400.0, 4410.0), (1_700_000_900, 4405.0, 4412.0)],
        dtype=[("time", "<i8"), ("open", "<f8"), ("high", "<f8")],
    )
    landed = decode(encode(rows))

    assert [row._fields for row in landed] == [("time", "open", "high")] * 2
    assert landed[1].open == 4405.0

    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame(landed)
    assert list(frame.columns) == ["time", "open", "high"]
    assert frame["time"].iloc[0] == 1_700_000_000


def test_a_naive_datetime_is_read_as_utc_on_both_sides():
    """MT5 reads a naive datetime as UTC; `datetime.timestamp()` reads one as
    local time.

    Encoding through `timestamp()` without carrying the awareness flag would
    shift every `copy_rates_range` window by the encoding machine's offset -
    silently, because the bars that come back are still bars. This desk has
    already read 87 seconds as 9 minutes by subtracting a UTC timestamp from a
    local one; the test is here so the wire cannot repeat it.
    """
    naive = dt.datetime(2026, 1, 1, 12, 0)
    aware = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.timezone.utc)

    assert decode(encode(naive)) == naive
    assert decode(encode(naive)).tzinfo is None
    assert decode(encode(aware)) == aware
    assert encode(naive)[1] == encode(aware)[1], "a naive datetime must mean UTC"


def test_a_shape_with_no_wire_form_is_refused():
    """Refusing beats degrading. A value the encoder passed through would cross
    as a reference, work for attribute access, and fail at serialisation - a
    long way from the call that produced it."""
    with pytest.raises(TypeError, match="no wire form"):
        encode(object())


# -------------------------------------------------------------- the terminal


@pytest.fixture(scope="module")
def served():
    """One server for the whole file.

    **A server per test is a race.** Each `listener(..., 0)` takes an
    ephemeral port, and closing one while a connection is still open leaves
    rpyc's accept thread reading a dead socket - which surfaces in whichever
    test runs next as `ConnectionRefusedError` or `Bad file descriptor`, a
    failure with nothing to do with the test reporting it. Measured: the same
    suite gave 6 errors on one run and 2 on the next with nothing changed.

    The service holds no state of its own - it reads `mt5` live - so sharing
    it across tests shares nothing that a test could dirty.
    """
    from app.services.terminal_server import listener

    server = listener("127.0.0.1", 0)
    threading.Thread(target=server.start, daemon=True).start()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture()
def remote(terminal, served):
    """A `_Remote` over a real connection to a real service.

    The service serves the fake terminal `conftest` installed, in this process,
    so `terminal.calls` can be read afterwards to see **what the far side
    actually received** - which is the only way to tell a decoded `dict` from a
    proxy that merely behaves like one.
    """
    from app.services.terminal_transport import _Remote

    remote = _Remote("127.0.0.1", served.port, 10.0)
    try:
        yield remote
    finally:
        with contextlib.suppress(Exception):
            remote._conn.close()


def test_the_connection_keeps_rpycs_restrictive_defaults(remote):
    """The control on every other test here.

    `order_send` working proves nothing on its own: it also worked with
    `allow_all_attrs=True`, which is what the security review flagged. What has
    to hold is that it works *without* it - so the policy is asserted directly
    on the live connection rather than read off the source.
    """
    config = remote._conn._config

    assert config["allow_all_attrs"] is False
    assert config["allow_public_attrs"] is False
    assert config["allow_pickle"] is False


def test_an_order_arrives_as_a_real_dict(remote, terminal):
    """The request is a `dict`, which brine refuses, so it used to arrive as a
    netref - and iterating one needs `keys` and `items`, public attributes that
    rpyc's default policy blocks. That is what `allow_all_attrs=True` was
    actually buying; the docstring claimed it was for `_fields`.

    Asserting on `terminal.calls` is the point: a netref would satisfy
    `request["action"]` and fail `isinstance(request, dict)`.
    """
    request = {"action": 1, "symbol": "XAUUSD", "volume": 0.01, "comment": "wire"}

    result = remote.order_send(request)

    assert result.retcode == 10009
    name, args, _ = terminal.calls[-1]
    assert name == "order_send"
    assert isinstance(args[0], dict), "the far side got a proxy, not a dict"
    assert args[0] == request


def test_a_datetime_argument_arrives_as_a_datetime(remote, terminal):
    """`copy_rates_range` takes two datetimes, which brine also refuses."""
    start = dt.datetime(2026, 1, 1, 0, 0)
    end = dt.datetime(2026, 1, 2, 0, 0)

    remote.copy_rates_range("XAUUSD", 15, start, end)

    name, args, _ = terminal.calls[-1]
    assert name == "copy_rates_range"
    assert isinstance(args[2], dt.datetime) and isinstance(args[3], dt.datetime)
    assert (args[2], args[3]) == (start, end)


def test_a_record_crosses_the_live_connection_whole(remote):
    """What `test_a_record_survives_as_a_record` asserts about the codec, over
    a socket, through the service, with `_asdict` reached the way a route
    reaches it."""
    info = remote.symbol_info("XAUUSD")

    assert info._asdict()["bid"] == 4400.0
    assert info.ask == 4400.5


def test_a_keyword_argument_reaches_the_terminal(remote, terminal):
    """`positions_get(ticket=...)` is the shape the fake refuses an unexpected
    keyword on, so a transport that dropped or renamed keywords would show up
    here rather than as an empty result."""
    remote.positions_get(ticket=7)

    name, _, kwargs = terminal.calls[-1]
    assert (name, kwargs) == ("positions_get", {"ticket": 7})


def test_a_constant_is_an_int_and_is_asked_for_once(remote):
    """`TIMEFRAME_M15` is compared against, indexed with and passed to the
    terminal, so it has to be an `int` rather than a proxy. The constants
    outnumber the functions, which is why they are cached."""
    assert isinstance(remote.ORDER_TYPE_BUY, int)
    assert remote.TRADE_RETCODE_DONE == 10009

    remote.TRADE_RETCODE_DONE
    assert remote._const["TRADE_RETCODE_DONE"] == 10009


def test_the_service_refuses_a_private_name(remote):
    """The access policy in one line. Without the underscore check, `const`
    would serve `__builtins__` and anything reachable from it, which is the
    remote-interpreter surface a three-method service exists to avoid."""
    with pytest.raises(AttributeError):
        remote._root.const("__builtins__")

    with pytest.raises(AttributeError):
        remote._root.const("_private")


def test_the_service_refuses_a_name_the_terminal_does_not_have(remote):
    with pytest.raises(AttributeError):
        remote.no_such_function


def test_a_constant_cannot_be_reached_through_call(remote):
    """Two methods and a clear error, rather than one method that guesses."""
    with pytest.raises(AttributeError):
        remote._root.call("TRADE_RETCODE_DONE", (), ())


# ------------------------------------------------------------- the allowlist


def test_the_allowlist_covers_every_call_site_in_the_application():
    """The pin on `CALLS`, re-derived from the source on every run.

    An allowlist written once drifts: a new `mt5.` call site works in-process
    and is refused over rpyc, which is a failure that only appears on the
    option nobody runs by default. So the names are read back out of the
    application and the difference is the assertion.

    Functions are told from constants by case - MT5's constants are upper and
    its functions are lower - rather than by asking the fake, which does not
    implement every function the application calls.
    """
    import re

    import app

    source = pathlib.Path(app.__file__).parent
    used = {
        name
        for path in source.rglob("*.py")
        for name in re.findall(r"\bmt5\.([A-Za-z_][A-Za-z0-9_]*)", path.read_text())
        if not name.isupper()
    }

    assert len(used) > 20, (
        f"the derivation found only {sorted(used)} - the regex or the source "
        f"path is wrong, and an empty derivation passes this test for the "
        f"wrong reason"
    )
    missing = used - CALLS
    assert not missing, (
        f"these are called in-process and would be refused over rpyc: "
        f"{sorted(missing)}. Add them to CALLS, and read the docstring there "
        f"first - initialize() launches the executable it is handed."
    )


def test_a_function_outside_the_allowlist_is_refused(remote):
    """`symbols_total` is a real MT5 function the application never calls, so
    it is not on the list. 'Any public function on the module' would include
    `initialize`, which launches the executable it is given."""
    with pytest.raises(AttributeError, match="not a function this service offers"):
        remote._root.call("symbols_total", (), ())


def test_initialize_uses_this_hosts_path_and_credentials(remote, terminal, monkeypatch):
    """The client names neither the terminal nor the account.

    `initialize(path)` **launches** the executable it is handed, and the path
    describes the terminal's machine, which the client is by construction not
    on. A client-supplied path is arbitrary code; client-supplied credentials
    point the terminal at an account of the caller's choosing.

    What is kept is the client's *intent*: `connector.py` calls without
    credentials first and retries with them only on failure, because
    re-sending them raises an "account changed" event that switches algo
    trading off.
    """
    from app.services import terminal_server

    monkeypatch.setattr(terminal_server, "TERMINAL_PATH", "C:\\real\\terminal64.exe")
    monkeypatch.setenv("MT5_LOGIN", "4242")
    monkeypatch.setenv("MT5_PASSWORD", "the-real-one")
    monkeypatch.setenv("MT5_SERVER", "Real-Server")

    remote.initialize("C:\\attacker\\payload.exe", portable=True)
    name, args, kwargs = terminal.calls[-1]
    assert name == "initialize"
    assert args == ("C:\\real\\terminal64.exe",), "the client chose the executable"
    assert "login" not in kwargs, "credentials were sent when none were asked for"

    remote.initialize("C:\\attacker\\payload.exe", login=1, password="theirs", server="Theirs")
    _, args, kwargs = terminal.calls[-1]
    assert args == ("C:\\real\\terminal64.exe",)
    assert (kwargs["login"], kwargs["password"], kwargs["server"]) == (
        4242,
        "the-real-one",
        "Real-Server",
    )


# ---------------------------------------------------------------- the secret


def test_the_right_secret_connects_and_a_wrong_one_does_not(terminal, monkeypatch):
    """Loopback is not an identity.

    Every process on this host can reach the port and the one that reaches it
    can place trades, so a shared secret answers "which local process". It
    crosses in clear, which is why it does not replace the loopback guard.
    """
    from app.services.terminal_server import listener
    from app.services.terminal_transport import _Remote

    monkeypatch.setenv("MT5_RPYC_SECRET", "the-secret")
    server = listener("127.0.0.1", 0)
    threading.Thread(target=server.start, daemon=True).start()
    opened = None
    try:
        opened = _Remote("127.0.0.1", server.port, 10.0)
        assert opened.TRADE_RETCODE_DONE == 10009

        monkeypatch.setenv("MT5_RPYC_SECRET", "not-the-secret")
        with pytest.raises(Exception):
            _Remote("127.0.0.1", server.port, 3.0)
    finally:
        if opened is not None:
            with contextlib.suppress(Exception):
                opened._conn.close()
        server.close()


def test_a_remote_bind_without_a_secret_is_refused(monkeypatch):
    """Allowing the network and offering no handshake at all is a port anybody
    can trade through."""
    from app.services.terminal_server import listener

    monkeypatch.setenv("MT5_RPYC_ALLOW_REMOTE", "1")
    monkeypatch.delenv("MT5_RPYC_SECRET", raising=False)
    with pytest.raises(ValueError, match="MT5_RPYC_SECRET"):
        listener("0.0.0.0", 18812)


# ------------------------------------------------------------- the two guards


def test_a_non_loopback_host_is_refused_without_the_flag(monkeypatch):
    """The channel is plaintext and unauthenticated and it carries trade
    instructions, so reaching a remote terminal is an SSH tunnel to 127.0.0.1,
    not an open port."""
    from app.services.terminal_transport import _Remote

    monkeypatch.delenv("MT5_RPYC_ALLOW_REMOTE", raising=False)
    with pytest.raises(ValueError, match="not loopback"):
        _Remote("198.51.100.7", 18812, 1.0)


def test_a_non_loopback_bind_is_refused_without_the_flag(monkeypatch):
    """The same guard on the serving side, which is the more dangerous one:
    anyone who reaches the port can place trades."""
    from app.services.terminal_server import listener

    monkeypatch.delenv("MT5_RPYC_ALLOW_REMOTE", raising=False)
    with pytest.raises(ValueError, match="not loopback"):
        listener("0.0.0.0", 18812)
