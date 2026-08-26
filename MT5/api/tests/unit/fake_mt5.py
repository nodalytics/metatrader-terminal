"""A stand-in for the `MetaTrader5` package, so the API can be tested anywhere.

The real package is a binding onto a running Windows terminal: it exists only
on Windows, and importing it does not simply fail elsewhere — it is not
installable at all, which is why `requirements.txt` marks it
`sys_platform == 'win32'`. Every module in `app` imports it at module scope, so
until now nothing in this project could be imported, let alone tested, without
a container and a live broker connection.

This module is that package's shape: the constants the code reads, the
functions it calls, and results that answer `_asdict()` like the real
namedtuples do. It is installed into `sys.modules` before `app` is imported —
see `conftest.py` — so the application under test is the real application,
unmodified, with only the terminal replaced.

**It is a fake, not a simulator.** It returns what it is told to return and
records what it was asked. It does not model fills, margin or the market, and
a test that needs those is an integration test against a real terminal, which
is what `tests/` already holds. What this catches is the large class of bugs
that have nothing to do with the market: a route that passes an argument the
terminal does not accept, a response that drops a field, a filter that filters
nothing.
"""

from __future__ import annotations

from collections import namedtuple

# ---------------------------------------------------------------- constants

TRADE_ACTION_DEAL = 1
TRADE_ACTION_SLTP = 6
TRADE_ACTION_PENDING = 5
TRADE_ACTION_REMOVE = 8
TRADE_ACTION_MODIFY = 7

ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
ORDER_TYPE_BUY_LIMIT = 2
ORDER_TYPE_SELL_LIMIT = 3
ORDER_TYPE_BUY_STOP = 4
ORDER_TYPE_SELL_STOP = 5
ORDER_TYPE_BUY_STOP_LIMIT = 6
ORDER_TYPE_SELL_STOP_LIMIT = 7

POSITION_TYPE_BUY = 0
POSITION_TYPE_SELL = 1

ORDER_TIME_GTC = 0
ORDER_TIME_DAY = 1
ORDER_TIME_SPECIFIED = 2
ORDER_TIME_SPECIFIED_DAY = 3

ORDER_FILLING_FOK = 0
ORDER_FILLING_IOC = 1
ORDER_FILLING_RETURN = 2

TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_PLACED = 10008
TRADE_RETCODE_INVALID_VOLUME = 10014

SYMBOL_TRADE_MODE_DISABLED = 0
SYMBOL_TRADE_MODE_LONGONLY = 1
SYMBOL_TRADE_MODE_SHORTONLY = 2
SYMBOL_TRADE_MODE_CLOSEONLY = 3
SYMBOL_TRADE_MODE_FULL = 4

# The real values, not placeholders. `MT5Timeframe` is an Enum, so two
# constants sharing a value would silently alias into one member and a
# timeframe would resolve to the wrong bars.
TIMEFRAME_M1 = 1
TIMEFRAME_M2 = 2
TIMEFRAME_M3 = 3
TIMEFRAME_M4 = 4
TIMEFRAME_M5 = 5
TIMEFRAME_M6 = 6
TIMEFRAME_M10 = 10
TIMEFRAME_M12 = 12
TIMEFRAME_M15 = 15
TIMEFRAME_M20 = 20
TIMEFRAME_M30 = 30
TIMEFRAME_H1 = 16385
TIMEFRAME_H2 = 16386
TIMEFRAME_H3 = 16387
TIMEFRAME_H4 = 16388
TIMEFRAME_H6 = 16390
TIMEFRAME_H8 = 16392
TIMEFRAME_H12 = 16396
TIMEFRAME_D1 = 16408
TIMEFRAME_W1 = 32769
TIMEFRAME_MN1 = 49153

COPY_TICKS_ALL = -1
COPY_TICKS_INFO = 1
COPY_TICKS_TRADE = 2

# ------------------------------------------------------------------- shapes
# Field sets match the real package's namedtuples closely enough that code
# reading them cannot tell the difference. Anything the app does not touch is
# left out rather than invented.

SymbolInfo = namedtuple(
    "SymbolInfo",
    "name path description digits point spread trade_mode trade_contract_size "
    "trade_tick_value trade_tick_size trade_stops_level volume_min volume_max "
    "volume_step filling_mode bid ask visible",
)
Tick = namedtuple("Tick", "time bid ask last volume time_msc flags volume_real")
Position = namedtuple(
    "Position",
    "ticket time time_msc time_update time_update_msc type magic identifier reason "
    "volume price_open sl tp price_current swap profit symbol comment external_id",
)
AccountInfo = namedtuple(
    "AccountInfo",
    "login trade_mode leverage limit_orders margin_so_mode trade_allowed trade_expert "
    "margin_free margin_level balance equity profit margin currency company server",
)
OrderResult = namedtuple(
    "OrderResult", "retcode deal order volume price bid ask comment request_id retcode_external"
)
TerminalInfo = namedtuple(
    "TerminalInfo", "community_account connected path build trade_allowed trade_expert"
)


def symbol(name="XAUUSD", bid=4400.0, ask=4400.5, **over):
    """A plausible symbol. Gold-shaped by default because that is what is traded."""
    fields = dict(
        name=name,
        path=f"Metals\\{name}",
        description=name,
        digits=2,
        point=0.01,
        spread=int(round((ask - bid) / 0.01)),
        trade_mode=SYMBOL_TRADE_MODE_FULL,
        trade_contract_size=100.0,
        trade_tick_value=1.0,
        trade_tick_size=0.01,
        trade_stops_level=0,
        volume_min=0.01,
        volume_max=50.0,
        volume_step=0.01,
        filling_mode=2,
        bid=bid,
        ask=ask,
        visible=True,
    )
    fields.update(over)
    return SymbolInfo(**fields)


def tick(bid=4400.0, ask=4400.5, when=1_700_000_000, **over):
    fields = dict(
        time=when,
        bid=bid,
        ask=ask,
        last=bid,
        volume=0,
        time_msc=when * 1000,
        flags=6,
        volume_real=0.0,
    )
    fields.update(over)
    return Tick(**fields)


def position(ticket=1, symbol_name="XAUUSD", magic=0, side=POSITION_TYPE_BUY, **over):
    fields = dict(
        ticket=ticket,
        time=1_700_000_000,
        time_msc=1_700_000_000_000,
        time_update=1_700_000_000,
        time_update_msc=1_700_000_000_000,
        type=side,
        magic=magic,
        identifier=ticket,
        reason=0,
        volume=0.05,
        price_open=4400.5,
        sl=4395.6,
        tp=4406.6,
        price_current=4401.0,
        swap=0.0,
        profit=2.5,
        symbol=symbol_name,
        comment="till scalp",
        external_id="",
    )
    fields.update(over)
    return Position(**fields)


def account(**over):
    fields = dict(
        login=123456,
        trade_mode=0,
        leverage=200,
        limit_orders=200,
        margin_so_mode=0,
        trade_allowed=True,
        trade_expert=True,
        margin_free=9_500.0,
        margin_level=0.0,
        balance=10_000.0,
        equity=10_002.5,
        profit=2.5,
        margin=500.0,
        currency="USD",
        company="Test Broker",
        server="Test-Demo",
    )
    fields.update(over)
    return AccountInfo(**fields)


def order_result(retcode=TRADE_RETCODE_DONE, **over):
    fields = dict(
        retcode=retcode,
        deal=555,
        order=555,
        volume=0.05,
        price=4400.5,
        bid=4400.0,
        ask=4400.5,
        comment="Request executed",
        request_id=1,
        retcode_external=0,
    )
    fields.update(over)
    return OrderResult(**fields)


# -------------------------------------------------------------------- state


class State:
    """What the fake terminal currently holds. Tests set these directly."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.initialized = True
        self.symbols = {"XAUUSD": symbol(), "BTCUSD": symbol("BTCUSD", 60_000.0, 60_010.0)}
        self.ticks = {name: tick(s.bid, s.ask) for name, s in self.symbols.items()}
        self.positions: list = []
        self.next_result = order_result()
        self.error = (0, "Success")
        #: Every call made, as (name, args, kwargs). The point of a fake: a
        #: route that passes an argument the terminal does not accept is
        #: invisible in a mock that accepts anything.
        self.calls: list = []
        self.selected: list = []


state = State()


def _record(name, *args, **kwargs):
    state.calls.append((name, args, kwargs))


# ---------------------------------------------------------------- functions


def initialize(*args, **kwargs):
    _record("initialize", *args, **kwargs)
    return state.initialized


def shutdown():
    _record("shutdown")
    return True


def last_error():
    return state.error


def version():
    return (500, 3815, "20 Mar 2024")


def terminal_info():
    return TerminalInfo(
        community_account=False,
        connected=True,
        path="C:\\",
        build=3815,
        trade_allowed=True,
        trade_expert=True,
    )


def account_info():
    _record("account_info")
    return account() if state.initialized else None


def symbols_get(*args, **kwargs):
    _record("symbols_get", *args, **kwargs)
    return tuple(state.symbols.values())


def symbol_info(name):
    _record("symbol_info", name)
    return state.symbols.get(name)


def symbol_info_tick(name):
    _record("symbol_info_tick", name)
    return state.ticks.get(name)


def symbol_select(name, enable=True):
    _record("symbol_select", name, enable)
    if name not in state.symbols:
        return False
    state.selected.append(name)
    return True


def positions_get(**kwargs):
    """Accepts only what the real one accepts, and that is the point.

    `positions_get` takes `symbol`, `group` or `ticket`. It does **not** take
    `magic`, and the real package raises on an unexpected keyword — so a caller
    filtering by magic here silently gets everything, or an error. This fake
    refuses the same argument so that a test can catch it.
    """
    _record("positions_get", **kwargs)
    allowed = {"symbol", "group", "ticket"}
    unexpected = set(kwargs) - allowed
    if unexpected:
        raise TypeError(
            f"positions_get() got an unexpected keyword argument {sorted(unexpected)[0]!r}"
        )
    found = list(state.positions)
    if "ticket" in kwargs:
        found = [p for p in found if p.ticket == kwargs["ticket"]]
    if "symbol" in kwargs:
        found = [p for p in found if p.symbol == kwargs["symbol"]]
    return tuple(found)


def order_send(request):
    _record("order_send", request)
    return state.next_result


def order_check(request):
    _record("order_check", request)
    return state.next_result


def copy_rates_from_pos(name, timeframe, start, count):
    _record("copy_rates_from_pos", name, timeframe, start, count)
    return []


def copy_rates_range(name, timeframe, start, end):
    _record("copy_rates_range", name, timeframe, start, end)
    return []


def copy_rates_from(name, timeframe, start, count):
    _record("copy_rates_from", name, timeframe, start, count)
    return []


def copy_ticks_from(name, start, count, flags):
    _record("copy_ticks_from", name, start, count, flags)
    return []


def copy_ticks_range(name, start, end, flags):
    _record("copy_ticks_range", name, start, end, flags)
    return []


def history_deals_get(*args, **kwargs):
    _record("history_deals_get", *args, **kwargs)
    return ()


def history_orders_get(*args, **kwargs):
    _record("history_orders_get", *args, **kwargs)
    return ()


def orders_get(*args, **kwargs):
    _record("orders_get", *args, **kwargs)
    return ()
