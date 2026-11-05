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

import datetime as dt
import threading

import numpy as np
import pytest

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


@pytest.fixture()
def remote(terminal):
    """A `_Remote` over a real connection to a real service.

    The service serves the fake terminal `conftest` installed, in this process,
    so `terminal.calls` can be read afterwards to see **what the far side
    actually received** - which is the only way to tell a decoded `dict` from a
    proxy that merely behaves like one.
    """
    from app.services.terminal_server import listener
    from app.services.terminal_transport import _Remote

    server = listener("127.0.0.1", 0)
    threading.Thread(target=server.start, daemon=True).start()
    try:
        yield _Remote("127.0.0.1", server.port, 10.0)
    finally:
        server.close()


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


def test_a_constant_asked_for_as_a_call_is_refused(remote):
    """Two methods and a clear error, rather than one method that guesses."""
    with pytest.raises(TypeError, match="constant"):
        remote._root.call("TRADE_RETCODE_DONE", (), ())


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
